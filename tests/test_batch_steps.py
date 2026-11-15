"""Tests for batch_steps.py: drive whole batches through the workflow.
A ScriptedClient plays both agents. Needs Postgres running: `docker compose up -d`.
"""

import json
from pathlib import Path

import psycopg
import pytest

from salesonboard.db import connect
from salesonboard.db.control import get_batch, get_latest_plan_version
from salesonboard.workflow.batch_status import InvalidMoveError
from salesonboard.workflow.batch_steps import (
    answer_questions,
    apply_fix,
    approve_mapping,
    approve_summary,
    create_batch,
    run_load,
    run_mapping,
    run_validation,
    stop_batch,
    update_batch,
)
from tests.fake_openai import ScriptedClient, answer, asks_for_tool

COMPANY1 = Path("demo_data/company1")
COMPANY2 = Path("demo_data/company2")


def read_plan(company):
    return json.loads((company / "ground_truth.json").read_text(encoding="utf-8"))


def mapping_reply(plan):
    """The fake mapping agent answers at once with this plan."""
    return ScriptedClient([answer(json.dumps(plan))])


def company2_plan_with_wrong_date_order():
    plan = read_plan(COMPANY2)
    for operation in plan["operations"]:
        if operation["op"] == "parse_date":
            operation["format"] = "%m/%d/%Y"
    return plan


FIXED_DATE = {
    "id": "op7",
    "op": "parse_date",
    "reason": "day comes first",
    "created_by": "investigator",
    "column": "order_date",
    "format": "%d/%m/%Y",
}


def investigator_finds_date_fix():
    return ScriptedClient(
        [
            asks_for_tool("simulate_plan_change", {"operations": [FIXED_DATE]}),
            answer(
                json.dumps(
                    {
                        "diagnosis": "Dates are day first.",
                        "change": {"operations": [FIXED_DATE]},
                    }
                )
            ),
        ]
    )


def batch_row(batch_id):
    with connect() as conn:
        return get_batch(conn, batch_id)


def latest_version(batch_id):
    with connect() as conn:
        return get_latest_plan_version(conn, batch_id)


def count_sales_lines(database_name):
    with connect(database_name) as conn:
        return conn.execute("SELECT count(*) FROM sales_lines").fetchone()[0]


# --- Create -----------------------------------------------------------------


def test_create_batch(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    batch = batch_row(batch_id)
    assert batch["status"] == "mapping"
    assert batch["row_count"] == 721
    assert len(batch["file_hash"]) == 64  # sha256 as hex


def test_second_active_batch_is_refused(registered_tenant):
    create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    with pytest.raises(psycopg.errors.UniqueViolation):
        create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")


def test_unknown_tenant_is_refused(db_conn):
    with pytest.raises(ValueError, match="Unknown tenant"):
        create_batch("nobody", COMPANY1 / "sales.csv")


# --- Whole journeys ---------------------------------------------------------


def test_happy_path_company1(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")

    assert run_mapping(batch_id, client=mapping_reply(read_plan(COMPANY1))) == "review_mapping"
    assert latest_version(batch_id)["created_by"] == "mapping_agent"

    assert approve_mapping(batch_id) == "validating"
    assert latest_version(batch_id)["action"] == "approved"

    assert run_validation(batch_id) == "summary"
    assert batch_row(batch_id)["rows_dropped"] == 4

    assert approve_summary(batch_id) == "loading"
    assert run_load(batch_id) == "loaded"

    batch = batch_row(batch_id)
    assert batch["rows_loaded"] == 717
    assert count_sales_lines(registered_tenant["database_name"]) == 717


def test_questions_path(registered_tenant):
    # The fake mapping agent is unsure about the Store column.
    plan = read_plan(COMPANY1)
    for column in plan["columns"]:
        if column["source"] == "Store":
            column.update(
                status="ambiguous",
                target=None,
                candidates=["store_id", "skip"],
                reason="Could be a store or a warehouse",
            )
    plan["operations"] = [op for op in plan["operations"] if op.get("source") != "Store"]

    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    assert run_mapping(batch_id, client=mapping_reply(plan)) == "questions"

    assert answer_questions(batch_id, {"Store": "store_id"}) == "review_mapping"
    version = latest_version(batch_id)
    assert version["action"] == "answered"
    store = [c for c in version["plan"]["columns"] if c["source"] == "Store"][0]
    assert store["target"] == "store_id"


def test_fix_path_company2(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY2 / "sales.csv")
    run_mapping(batch_id, client=mapping_reply(company2_plan_with_wrong_date_order()))
    approve_mapping(batch_id)

    # R2 fails, the investigator finds the date fix.
    assert run_validation(batch_id, client=investigator_finds_date_fix()) == "fix_problems"
    version = latest_version(batch_id)
    assert version["created_by"] == "investigator"
    assert version["note"] == "Dates are day first."
    assert batch_row(batch_id)["investigator_rounds"] == 1

    # The customer applies it, and the fixed plan passes.
    assert apply_fix(batch_id) == "validating"
    assert run_validation(batch_id) == "summary"


def test_round_limit_sends_batch_to_support(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY2 / "sales.csv")
    run_mapping(batch_id, client=mapping_reply(company2_plan_with_wrong_date_order()))
    approve_mapping(batch_id)
    with connect() as conn:
        update_batch(conn, batch_id, investigator_rounds=2)

    # No client given: the investigator must not run (it would need the real API).
    assert run_validation(batch_id) == "support"


def test_no_fix_found_sends_batch_to_support(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY2 / "sales.csv")
    run_mapping(batch_id, client=mapping_reply(company2_plan_with_wrong_date_order()))
    approve_mapping(batch_id)

    gives_up = ScriptedClient(
        [answer(json.dumps({"diagnosis": "No idea.", "change": {"operations": []}}))]
    )
    assert run_validation(batch_id, client=gives_up) == "support"


# --- Rules ------------------------------------------------------------------


def test_step_at_the_wrong_status_is_refused(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    run_mapping(batch_id, client=mapping_reply(read_plan(COMPANY1)))  # now review_mapping

    with pytest.raises(InvalidMoveError):
        approve_summary(batch_id)  # load before validation
    with pytest.raises(InvalidMoveError):
        run_load(batch_id)
    assert batch_row(batch_id)["status"] == "review_mapping"


def test_agent_does_not_run_at_the_wrong_status(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    run_mapping(batch_id, client=mapping_reply(read_plan(COMPANY1)))

    client = ScriptedClient([])  # would fail if the agent called it
    with pytest.raises(InvalidMoveError):
        run_mapping(batch_id, client=client)
    assert client.requests == []


def test_answers_must_cover_exactly_the_unclear_columns(registered_tenant):
    plan = read_plan(COMPANY1)
    for column in plan["columns"]:
        if column["source"] == "Store":
            column.update(status="ambiguous", target=None, candidates=["store_id", "skip"])
            column["reason"] = "unsure"
    plan["operations"] = [op for op in plan["operations"] if op.get("source") != "Store"]

    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    run_mapping(batch_id, client=mapping_reply(plan))

    with pytest.raises(ValueError, match="unclear columns"):
        answer_questions(batch_id, {"SalesRep": "store_id"})
    assert batch_row(batch_id)["status"] == "questions"  # nothing changed


def test_stop_batch_frees_the_customer(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    run_mapping(batch_id, client=mapping_reply(read_plan(COMPANY1)))  # waiting for the customer
    assert stop_batch(batch_id) == "stopped"
    create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")  # no error


def test_batch_cannot_be_stopped_while_an_agent_runs(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")  # mapping
    with pytest.raises(InvalidMoveError):
        stop_batch(batch_id)
