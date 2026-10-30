"""The control database: tenants, batches and the audit trail."""

from pathlib import Path

import psycopg

CONTROL_SQL = Path(__file__).parent / "control.sql"

CONTROL_TABLES = [
    "tenants",
    "batches",
    "plan_versions",
    "validation_results",
    "llm_calls",
    "support_tickets",
]


def create_control_tables(conn: psycopg.Connection):
    """Create every control table that does not exist yet. Safe to run twice."""
    conn.execute(CONTROL_SQL.read_text(encoding="utf-8"))


def register_tenant(
    conn: psycopg.Connection,
    tenant_id: str,
    name: str,
    username: str,
    password_hash: str,
    database_name: str,
):
    """Add a customer. Does nothing if the tenant is already registered."""
    conn.execute(
        """
        INSERT INTO tenants (tenant_id, name, username, password_hash, database_name)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id) DO NOTHING
        """,
        (tenant_id, name, username, password_hash, database_name),
    )


def get_tenant(conn: psycopg.Connection, tenant_id: str) -> dict | None:
    row = conn.execute(
        "SELECT tenant_id, name, username, database_name FROM tenants WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    if row is None:
        return None
    return {"tenant_id": row[0], "name": row[1], "username": row[2], "database_name": row[3]}
