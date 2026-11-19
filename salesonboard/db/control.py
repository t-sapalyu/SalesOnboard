"""The control database: tenants, batches and the audit trail."""

from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

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


def get_login(conn: psycopg.Connection, username: str) -> dict | None:
    """The tenant and password hash for a login name. Only used to check a password."""
    row = conn.execute(
        "SELECT tenant_id, name, password_hash FROM tenants WHERE username = %s",
        (username,),
    ).fetchone()
    if row is None:
        return None
    return {"tenant_id": row[0], "name": row[1], "password_hash": row[2]}


# ---------------------------------------------------------------------------
# Batches and their audit trail. The workflow rules (which status may follow
# which) are in workflow/batch_status.py; these functions only read and write.
# ---------------------------------------------------------------------------

BATCH_COLUMNS = [
    "batch_id",
    "tenant_id",
    "status",
    "file_name",
    "file_path",
    "file_hash",
    "row_count",
    "rows_loaded",
    "rows_dropped",
    "cost_eur",
    "investigator_rounds",
]


def insert_batch(
    conn: psycopg.Connection,
    batch_id: str,
    tenant_id: str,
    status: str,
    file_name: str,
    file_path: str,
    file_hash: str,
    row_count: int,
):
    conn.execute(
        """
        INSERT INTO batches
            (batch_id, tenant_id, status, file_name, file_path, file_hash, row_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (batch_id, tenant_id, status, file_name, file_path, file_hash, row_count),
    )


def get_batch(conn: psycopg.Connection, batch_id: str, lock: bool = False) -> dict | None:
    """One batch as a dict. lock=True keeps other connections from changing
    the row until this transaction ends (SELECT ... FOR UPDATE)."""
    query = f"SELECT {', '.join(BATCH_COLUMNS)} FROM batches WHERE batch_id = %s"
    if lock:
        query += " FOR UPDATE"
    row = conn.execute(query, (batch_id,)).fetchone()
    if row is None:
        return None
    return dict(zip(BATCH_COLUMNS, row, strict=True))


def update_batch(conn: psycopg.Connection, batch_id: str, **values):
    """Set some columns of a batch, e.g. update_batch(conn, id, status="summary").

    The column names come from our own code (checked against BATCH_COLUMNS),
    never from user input, so building the SQL from them is safe.
    """
    for column in values:
        if column not in BATCH_COLUMNS:
            raise ValueError(f"Unknown batch column {column!r}")
    assignments = ", ".join(f"{column} = %s" for column in values)
    conn.execute(
        f"UPDATE batches SET {assignments}, updated_at = now() WHERE batch_id = %s",
        (*values.values(), batch_id),
    )


def save_plan_version(
    conn: psycopg.Connection,
    batch_id: str,
    plan: dict,
    created_by: str,
    action: str,
    note: str | None = None,
) -> int:
    """Store a new version of the batch's plan. Returns its version number."""
    row = conn.execute(
        "SELECT coalesce(max(version), 0) FROM plan_versions WHERE batch_id = %s",
        (batch_id,),
    ).fetchone()
    version = row[0] + 1
    conn.execute(
        """
        INSERT INTO plan_versions (batch_id, version, plan, created_by, action, note)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (batch_id, version, Jsonb(plan), created_by, action, note),
    )
    return version


def get_latest_plan_version(conn: psycopg.Connection, batch_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT version, plan, created_by, action, note FROM plan_versions
        WHERE batch_id = %s ORDER BY version DESC LIMIT 1
        """,
        (batch_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "version": row[0],
        "plan": row[1],
        "created_by": row[2],
        "action": row[3],
        "note": row[4],
    }


def save_validation_results(conn: psycopg.Connection, batch_id: str, results: list) -> int:
    """Store one validation run (a list of RuleResult). Returns the run number."""
    row = conn.execute(
        "SELECT coalesce(max(run), 0) FROM validation_results WHERE batch_id = %s",
        (batch_id,),
    ).fetchone()
    run = row[0] + 1
    for result in results:
        conn.execute(
            """
            INSERT INTO validation_results (batch_id, run, rule, passed, failing_rows, message)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (batch_id, run, result.rule, result.passed, Jsonb(result.failing_rows), result.message),
        )
    return run


def get_latest_validation_results(conn: psycopg.Connection, batch_id: str) -> list[dict]:
    """The rule results of the batch's last validation run; empty if none yet."""
    rows = conn.execute(
        """
        SELECT run, rule, passed, failing_rows, message FROM validation_results
        WHERE batch_id = %s
          AND run = (SELECT max(run) FROM validation_results WHERE batch_id = %s)
        ORDER BY rule
        """,
        (batch_id, batch_id),
    ).fetchall()
    results = []
    for run, rule, passed, failing_rows, message in rows:
        results.append(
            {
                "run": run,
                "rule": rule,
                "passed": passed,
                "failing_rows": failing_rows,
                "message": message,
            }
        )
    return results


def insert_support_ticket(
    conn: psycopg.Connection,
    batch_id: str,
    tenant_id: str,
    step: str,
    reason: str,
    note: str | None = None,
) -> int:
    """Store a support ticket. Returns its ticket number."""
    row = conn.execute(
        """
        INSERT INTO support_tickets (batch_id, tenant_id, step, reason, note)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING ticket_id
        """,
        (batch_id, tenant_id, step, reason, note),
    ).fetchone()
    return row[0]


def get_support_tickets(conn: psycopg.Connection, batch_id: str) -> list[dict]:
    """The batch's tickets, oldest first."""
    rows = conn.execute(
        """
        SELECT ticket_id, step, reason, note, status FROM support_tickets
        WHERE batch_id = %s ORDER BY ticket_id
        """,
        (batch_id,),
    ).fetchall()
    tickets = []
    for ticket_id, step, reason, note, status in rows:
        tickets.append(
            {"ticket_id": ticket_id, "step": step, "reason": reason, "note": note, "status": status}
        )
    return tickets
