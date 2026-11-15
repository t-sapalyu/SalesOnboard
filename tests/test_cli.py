"""Needs Postgres running: `docker compose up -d`. Skipped otherwise."""

import json
from pathlib import Path

import pytest

from salesonboard.cli import load_file, main, parse_answers, start
from salesonboard.db import connect
from tests.fake_openai import ScriptedClient, answer

DEMO_DATA = Path("demo_data")

# The registered_tenant fixture is in conftest.py.


def count_rows(database_name):
    with connect(database_name) as conn:
        return conn.execute("SELECT count(*) FROM sales_lines").fetchone()[0]


def test_company1_file_is_loaded(registered_tenant):
    summary = load_file(
        registered_tenant["tenant_id"],
        DEMO_DATA / "company1" / "sales.csv",
        DEMO_DATA / "company1" / "ground_truth.json",
    )
    assert summary.loaded
    assert summary.rows_in == 721
    assert summary.rows_dropped == 4
    assert summary.rows_loaded == 717
    assert count_rows(registered_tenant["database_name"]) == 717


def test_failed_rule_loads_nothing(registered_tenant, tmp_path):
    # company2's plan with the wrong date order: R2 fails.
    plan = json.loads((DEMO_DATA / "company2" / "ground_truth.json").read_text(encoding="utf-8"))
    for operation in plan["operations"]:
        if operation["op"] == "parse_date":
            operation["format"] = "%m/%d/%Y"
    wrong_plan = tmp_path / "wrong_plan.json"
    wrong_plan.write_text(json.dumps(plan), encoding="utf-8")

    summary = load_file(
        registered_tenant["tenant_id"], DEMO_DATA / "company2" / "sales.csv", wrong_plan
    )
    assert not summary.loaded
    assert summary.rows_loaded == 0
    assert count_rows(registered_tenant["database_name"]) == 0


def test_unknown_tenant_is_refused(db_conn):
    with pytest.raises(ValueError, match="Unknown tenant"):
        load_file("nobody", DEMO_DATA / "company1" / "sales.csv", Path("unused.json"))


def test_command_line_prints_summary_and_returns_0(registered_tenant, capsys):
    exit_code = main(
        [
            "load",
            registered_tenant["tenant_id"],
            str(DEMO_DATA / "company1" / "sales.csv"),
            "--plan",
            str(DEMO_DATA / "company1" / "ground_truth.json"),
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "R4" in output
    assert "Loaded 717 rows" in output


# --- Batch workflow commands ------------------------------------------------


def test_drive_a_batch_from_the_command_line(registered_tenant, capsys):
    # start runs the mapping agent; a fake one answers with the correct plan.
    plan_json = (DEMO_DATA / "company1" / "ground_truth.json").read_text(encoding="utf-8")
    batch_id = start(
        registered_tenant["tenant_id"],
        DEMO_DATA / "company1" / "sales.csv",
        client=ScriptedClient([answer(plan_json)]),
    )

    assert main(["show", batch_id]) == 0
    output = capsys.readouterr().out
    assert "status: review_mapping" in output
    assert "OrderDate            -> order_date" in output
    assert "Next: approve" in output

    assert main(["approve", batch_id]) == 0  # use this plan -> validation passes
    assert "status: summary" in capsys.readouterr().out

    assert main(["approve", batch_id]) == 0  # load my data
    output = capsys.readouterr().out
    assert "status: loaded" in output
    assert "Loaded 717 rows (4 dropped)." in output
    assert count_rows(registered_tenant["database_name"]) == 717


def test_parse_answers():
    assert parse_answers(["Store=store_id", "Notes=skip"]) == {"Store": "store_id", "Notes": None}


def test_send_to_support_from_the_command_line(registered_tenant, capsys):
    plan_json = (DEMO_DATA / "company1" / "ground_truth.json").read_text(encoding="utf-8")
    batch_id = start(
        registered_tenant["tenant_id"],
        DEMO_DATA / "company1" / "sales.csv",
        client=ScriptedClient([answer(plan_json)]),
    )

    assert main(["support", batch_id, "--note", "Prices look wrong"]) == 0
    output = capsys.readouterr().out
    assert "status: support" in output
    assert "Support ticket #" in output
    assert "note: Prices look wrong" in output
