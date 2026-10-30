"""Needs Postgres running: `docker compose up -d`. Skipped otherwise."""

import psycopg
import pytest

from salesonboard.db.control import CONTROL_TABLES, create_control_tables


def add_tenant(conn, tenant_id):
    conn.execute(
        """
        INSERT INTO tenants (tenant_id, name, username, password_hash, database_name)
        VALUES (%s, %s, %s, 'not-a-real-hash', %s)
        """,
        (tenant_id, f"Test {tenant_id}", tenant_id, f"tenant_{tenant_id}"),
    )


def add_batch(conn, batch_id, tenant_id, status):
    conn.execute(
        """
        INSERT INTO batches (batch_id, tenant_id, status, file_name, file_path, file_hash)
        VALUES (%s, %s, %s, 'sales.csv', 'uploads/sales.csv', 'abc')
        """,
        (batch_id, tenant_id, status),
    )


def test_all_control_tables_exist(control_db):
    rows = control_db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    existing = {row[0] for row in rows}
    for table in CONTROL_TABLES:
        assert table in existing


def test_creating_tables_twice_is_safe(control_db):
    create_control_tables(control_db)  # the fixture already ran it once


def test_second_active_batch_for_same_tenant_is_rejected(control_db):
    add_tenant(control_db, "test_t1")
    add_batch(control_db, "test_b1", "test_t1", "uploaded")

    # transaction() makes a savepoint, so the failed insert does not break the
    # connection for the rest of the test.
    with pytest.raises(psycopg.errors.UniqueViolation), control_db.transaction():
        add_batch(control_db, "test_b2", "test_t1", "uploaded")


def test_finished_batch_does_not_block_a_new_upload(control_db):
    add_tenant(control_db, "test_t1")
    add_batch(control_db, "test_b1", "test_t1", "loaded")
    add_batch(control_db, "test_b2", "test_t1", "uploaded")  # no error


def test_different_tenants_can_upload_at_the_same_time(control_db):
    add_tenant(control_db, "test_t1")
    add_tenant(control_db, "test_t2")
    add_batch(control_db, "test_b1", "test_t1", "uploaded")
    add_batch(control_db, "test_b2", "test_t2", "uploaded")  # no error


def test_ticket_status_must_be_open_or_closed(control_db):
    add_tenant(control_db, "test_t1")
    add_batch(control_db, "test_b1", "test_t1", "uploaded")

    with pytest.raises(psycopg.errors.CheckViolation), control_db.transaction():
        control_db.execute(
            """
            INSERT INTO support_tickets (batch_id, tenant_id, step, reason, status)
            VALUES ('test_b1', 'test_t1', 'mapping', 'customer', 'pending')
            """
        )
