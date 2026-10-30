"""Needs Postgres running: `docker compose up -d`. Skipped otherwise."""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest

from salesonboard import schema
from salesonboard.db import connect
from salesonboard.pipeline.executor import execute
from salesonboard.pipeline.source_file import read_source_file
from salesonboard.pipeline.writer import RoutingError, write_rows
from salesonboard.transform_plan import TransformPlan


def make_rows(tenant_id, count, batch_id="b1"):
    """Canonical rows built by hand, like the executor gives them."""
    rows = []
    for source_row in range(1, count + 1):
        rows.append(
            {
                "tenant_id": tenant_id,
                "batch_id": batch_id,
                "source_row": source_row,
                "order_id": f"A{source_row}",
                "order_date": date(2025, 1, 6),
                "product_id": "SKU-1",
                "store_id": "STORE-01",
                "quantity": 2.0,
                "unit_price": 4.99,
            }
        )
    return pd.DataFrame(rows, columns=[field.name for field in schema.FIELDS], dtype=object)


def count_rows(database_name):
    with connect(database_name) as conn:
        return conn.execute("SELECT count(*) FROM sales_lines").fetchone()[0]


# --- Happy path -------------------------------------------------------------


def test_writes_all_rows(test_tenant):
    rows = make_rows(test_tenant["tenant_id"], 3)
    written = write_rows(test_tenant["database_name"], test_tenant["tenant_id"], rows)
    assert written == 3
    assert count_rows(test_tenant["database_name"]) == 3


def test_stored_values_match_what_was_written(test_tenant):
    rows = make_rows(test_tenant["tenant_id"], 1)
    rows.loc[0, "store_id"] = None  # optional field left empty
    write_rows(test_tenant["database_name"], test_tenant["tenant_id"], rows)

    with connect(test_tenant["database_name"]) as conn:
        stored = conn.execute(
            "SELECT source_row, order_date, store_id, quantity, unit_price FROM sales_lines"
        ).fetchone()
    source_row, order_date, store_id, quantity, unit_price = stored
    assert source_row == 1
    assert order_date == date(2025, 1, 6)
    assert store_id is None
    assert float(quantity) == 2.0
    assert float(unit_price) == 4.99


def test_executor_output_for_company1_can_be_written(test_tenant):
    source = read_source_file(Path("demo_data/company1/sales.csv"))
    plan_text = Path("demo_data/company1/ground_truth.json").read_text(encoding="utf-8")
    plan = TransformPlan.model_validate(json.loads(plan_text))
    result = execute(source.data, plan, tenant_id=test_tenant["tenant_id"], batch_id="b1")

    written = write_rows(test_tenant["database_name"], test_tenant["tenant_id"], result.rows)
    assert written == len(result.rows)
    assert count_rows(test_tenant["database_name"]) == len(result.rows)


# --- Routing guard ----------------------------------------------------------


def test_wrong_tenant_fails_loudly_and_writes_nothing(test_tenant):
    # The batch says company1, but the database belongs to the test tenant.
    rows = make_rows("company1", 3)
    with pytest.raises(RoutingError, match="Database belongs to"):
        write_rows(test_tenant["database_name"], "company1", rows)
    assert count_rows(test_tenant["database_name"]) == 0


def test_rows_from_another_tenant_are_refused(test_tenant):
    rows = make_rows(test_tenant["tenant_id"], 3)
    rows.loc[1, "tenant_id"] = "company2"
    with pytest.raises(RoutingError, match="cannot be written"):
        write_rows(test_tenant["database_name"], test_tenant["tenant_id"], rows)
    assert count_rows(test_tenant["database_name"]) == 0


# --- One transaction --------------------------------------------------------


def test_error_halfway_leaves_zero_rows(test_tenant):
    rows = make_rows(test_tenant["tenant_id"], 5)
    rows.loc[2, "order_id"] = None  # row 3 breaks NOT NULL; rows 1-2 were already sent
    with pytest.raises(psycopg.errors.NotNullViolation):
        write_rows(test_tenant["database_name"], test_tenant["tenant_id"], rows)
    assert count_rows(test_tenant["database_name"]) == 0


def test_same_batch_cannot_be_loaded_twice(test_tenant):
    rows = make_rows(test_tenant["tenant_id"], 3)
    write_rows(test_tenant["database_name"], test_tenant["tenant_id"], rows)
    with pytest.raises(psycopg.errors.UniqueViolation):
        write_rows(test_tenant["database_name"], test_tenant["tenant_id"], rows)
    assert count_rows(test_tenant["database_name"]) == 3
