"""Needs Postgres running: `docker compose up -d`. Skipped otherwise."""

import pytest

from salesonboard import schema
from salesonboard.db import connect
from salesonboard.db.control import get_tenant, register_tenant
from salesonboard.db.tenant import (
    SCHEMA_VERSION,
    TEMPLATE_DB,
    create_template_database,
    create_tenant_database,
    database_name_for,
)

TEST_TENANT = "pytest_tenant"

# Postgres names for the types used in schema.py
POSTGRES_TYPES = {"text": "text", "integer": "integer", "numeric": "numeric", "date": "date"}


def test_database_name_for_valid_tenant():
    assert database_name_for("company1") == "tenant_company1"


@pytest.mark.parametrize("bad_id", ["Company1", "a-b", "x; DROP DATABASE control", ""])
def test_database_name_for_rejects_unsafe_ids(bad_id):
    with pytest.raises(ValueError):
        database_name_for(bad_id)


def test_sales_lines_columns_match_schema(db_conn):
    create_template_database()
    with connect(TEMPLATE_DB) as conn:
        rows = conn.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'sales_lines'
            """
        ).fetchall()

    columns = {}
    for column_name, data_type, is_nullable in rows:
        columns[column_name] = (data_type, is_nullable)

    for field in schema.FIELDS:
        assert field.name in columns, f"sales_lines has no column {field.name}"
        data_type, is_nullable = columns[field.name]
        assert data_type == POSTGRES_TYPES[field.type], field.name
        expected_nullable = "NO" if field.required else "YES"
        assert is_nullable == expected_nullable, field.name


def test_new_tenant_database_knows_its_tenant(test_tenant):
    with connect(test_tenant["database_name"]) as conn:
        rows = conn.execute("SELECT tenant_id, schema_version FROM tenant_meta").fetchall()
    assert rows == [(test_tenant["tenant_id"], SCHEMA_VERSION)]


def test_creating_tenant_database_twice_is_safe(test_tenant):
    create_tenant_database(test_tenant["tenant_id"])
    with connect(test_tenant["database_name"]) as conn:
        count = conn.execute("SELECT count(*) FROM tenant_meta").fetchone()[0]
    assert count == 1


def test_register_tenant(control_db):
    register_tenant(control_db, TEST_TENANT, "Test", "test_user", "hash", "tenant_x")
    register_tenant(control_db, TEST_TENANT, "Test", "test_user", "hash", "tenant_x")  # no error

    tenant = get_tenant(control_db, TEST_TENANT)
    assert tenant["database_name"] == "tenant_x"
    assert get_tenant(control_db, "nobody") is None
