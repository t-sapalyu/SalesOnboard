"""Needs Postgres running: `docker compose up -d`. Skipped otherwise."""

import json
from pathlib import Path

import pytest

from salesonboard.cli import load_file, main
from salesonboard.db import connect
from salesonboard.db.control import create_control_tables, register_tenant

DEMO_DATA = Path("demo_data")


@pytest.fixture
def registered_tenant(test_tenant):
    """The throwaway test tenant, also registered (committed) in the control
    database, because load_file looks it up with its own connection."""
    with connect() as conn:
        create_control_tables(conn)
        register_tenant(
            conn,
            test_tenant["tenant_id"],
            "Pytest tenant",
            "pytest_user",
            "not-a-real-hash",
            test_tenant["database_name"],
        )
    yield test_tenant
    with connect() as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id = %s", (test_tenant["tenant_id"],))


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
