"""Tests for support_tickets.py: manual tickets and automatic stops.
Needs Postgres running: `docker compose up -d`.
"""

import json

import pytest

from salesonboard.db import connect
from salesonboard.db.control import get_support_tickets, update_batch
from salesonboard.workflow.batch_status import InvalidMoveError
from salesonboard.workflow.batch_steps import (
    approve_mapping,
    create_batch,
    run_mapping,
    run_validation,
)
from salesonboard.workflow.support_tickets import send_to_support
from tests.fake_openai import ScriptedClient, answer
from tests.test_batch_steps import (
    COMPANY1,
    COMPANY2,
    FIXED_DATE,
    batch_row,
    company2_plan_with_wrong_date_order,
    mapping_reply,
    read_plan,
)


def tickets(batch_id):
    with connect() as conn:
        return get_support_tickets(conn, batch_id)


def company2_batch_with_failing_rule(tenant_id):
    """A company2 batch at "validating", with a plan whose date rule will fail."""
    batch_id = create_batch(tenant_id, COMPANY2 / "sales.csv")
    run_mapping(batch_id, client=mapping_reply(company2_plan_with_wrong_date_order()))
    approve_mapping(batch_id)
    return batch_id


# --- Manual: the customer presses "Send to support" ---------------------------


def test_customer_sends_batch_to_support(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    run_mapping(batch_id, client=mapping_reply(read_plan(COMPANY1)))  # review_mapping

    ticket_id = send_to_support(batch_id, "customer", note="SalesRep should be loaded")

    assert batch_row(batch_id)["status"] == "support"
    [ticket] = tickets(batch_id)
    assert ticket["ticket_id"] == ticket_id
    assert ticket["step"] == "review_mapping"
    assert ticket["reason"] == "customer"
    assert ticket["note"] == "SalesRep should be loaded"
    assert ticket["status"] == "open"

    # The batch is paused, so the customer may upload a new file.
    create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")


def test_customer_cannot_send_while_an_agent_runs(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")  # mapping
    with pytest.raises(InvalidMoveError):
        send_to_support(batch_id, "customer")
    assert tickets(batch_id) == []


def test_finished_batch_cannot_get_a_ticket(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    run_mapping(batch_id, client=mapping_reply(read_plan(COMPANY1)))
    send_to_support(batch_id, "customer")
    with pytest.raises(InvalidMoveError):
        send_to_support(batch_id, "customer")  # already at support
    assert len(tickets(batch_id)) == 1


def test_unknown_reason_is_refused(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    with pytest.raises(ValueError, match="Unknown support reason"):
        send_to_support(batch_id, "bored")


# --- Automatic: the system cannot continue on its own -------------------------


def test_cost_limit_during_mapping(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    with connect() as conn:
        update_batch(conn, batch_id, cost_eur=1)  # the whole budget is already spent

    client = ScriptedClient([])
    assert run_mapping(batch_id, client=client) == "support"
    assert client.requests == []  # the call was refused before reaching the API

    [ticket] = tickets(batch_id)
    assert ticket["reason"] == "cost_limit"
    assert ticket["step"] == "mapping"


def test_invalid_output_during_mapping(registered_tenant):
    batch_id = create_batch(registered_tenant["tenant_id"], COMPANY1 / "sales.csv")
    bad_answers = ScriptedClient([answer("not json"), answer("still not json")])

    assert run_mapping(batch_id, client=bad_answers) == "support"
    assert tickets(batch_id)[0]["reason"] == "invalid_output"


def test_investigator_proposes_a_change_it_did_not_simulate(registered_tenant):
    batch_id = company2_batch_with_failing_rule(registered_tenant["tenant_id"])
    skips_simulation = ScriptedClient(
        [answer(json.dumps({"diagnosis": "Day first.", "change": {"operations": [FIXED_DATE]}}))]
    )

    assert run_validation(batch_id, client=skips_simulation) == "support"
    [ticket] = tickets(batch_id)
    assert ticket["reason"] == "invalid_output"
    assert ticket["step"] == "validating"


def test_round_limit_creates_a_ticket(registered_tenant):
    batch_id = company2_batch_with_failing_rule(registered_tenant["tenant_id"])
    with connect() as conn:
        update_batch(conn, batch_id, investigator_rounds=2)

    assert run_validation(batch_id) == "support"
    assert tickets(batch_id)[0]["reason"] == "round_limit"


def test_no_fix_creates_a_ticket_with_the_diagnosis(registered_tenant):
    batch_id = company2_batch_with_failing_rule(registered_tenant["tenant_id"])
    gives_up = ScriptedClient(
        [answer(json.dumps({"diagnosis": "No idea.", "change": {"operations": []}}))]
    )

    assert run_validation(batch_id, client=gives_up) == "support"
    [ticket] = tickets(batch_id)
    assert ticket["reason"] == "no_fix"
    assert ticket["note"] == "No idea."
