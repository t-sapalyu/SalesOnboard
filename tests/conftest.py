"""Shared test fixtures. pytest loads this file automatically."""

import psycopg
import pytest
from psycopg import sql

from salesonboard.db import connect
from salesonboard.db.control import create_control_tables, register_tenant
from salesonboard.db.tenant import create_template_database, create_tenant_database


@pytest.fixture
def db_conn():
    """A connection to the control database, or skip if Postgres is not running.

    Everything a test does runs inside one transaction that is rolled back at
    the end, so tests never leave rows behind.
    """
    try:
        conn = connect()
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running (docker compose up -d)")
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def control_db(db_conn):
    """The control database with all its tables."""
    create_control_tables(db_conn)
    return db_conn


@pytest.fixture
def test_tenant(db_conn):
    """A throwaway customer database, dropped after the test.

    Returns {"tenant_id": ..., "database_name": ...}.
    db_conn is only here to skip the test when Postgres is not running.
    """
    tenant_id = "pytest_tenant"
    create_template_database()
    database_name = create_tenant_database(tenant_id)

    yield {"tenant_id": tenant_id, "database_name": database_name}

    # CREATE DATABASE cannot be rolled back, so we drop it ourselves.
    # FORCE closes any connection still open to it.
    with connect(autocommit=True) as conn:
        query = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)")
        conn.execute(query.format(sql.Identifier(database_name)))


@pytest.fixture
def batch(db_conn):
    """A tenant and a batch in the control database, for LLM calls to log to.

    Committed (not rolled back), because call_llm uses its own connections.
    Deleted again after the test. Returns the batch_id.
    """
    tenant_id = "pytest_llm"
    batch_id = "pytest-llm-batch"
    with connect() as conn:
        create_control_tables(conn)
        register_tenant(conn, tenant_id, "Pytest LLM", "pytest_llm", "x", "tenant_pytest_llm")
        conn.execute(
            """
            INSERT INTO batches (batch_id, tenant_id, status, file_name, file_path, file_hash)
            VALUES (%s, %s, 'mapping', 'sales.csv', 'unused', 'unused')
            """,
            (batch_id, tenant_id),
        )
    yield batch_id
    with connect() as conn:
        conn.execute("DELETE FROM llm_calls WHERE batch_id = %s", (batch_id,))
        conn.execute("DELETE FROM batches WHERE batch_id = %s", (batch_id,))
        conn.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
