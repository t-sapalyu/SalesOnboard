"""Customer databases: one per tenant, each created as a copy of a template.

Why a template: Postgres can copy a whole database in one statement
(CREATE DATABASE ... TEMPLATE ...), so every customer starts with exactly
the same tables.
"""

import re
from pathlib import Path

from psycopg import sql

from salesonboard.db import connect
from salesonboard.db.control import create_control_tables, register_tenant
from salesonboard.passwords import hash_password

TENANT_SQL = Path(__file__).parent / "tenant.sql"
TEMPLATE_DB = "tenant_template"
SCHEMA_VERSION = 1

# Tenant ids become part of a database name, so we allow only safe characters.
TENANT_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


def database_name_for(tenant_id: str) -> str:
    if not TENANT_ID_PATTERN.match(tenant_id):
        raise ValueError(f"Tenant id {tenant_id!r} may only use a-z, 0-9 and _")
    return f"tenant_{tenant_id}"


def database_exists(name: str) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
    return row is not None


def create_database(name: str, template: str | None = None):
    # sql.Identifier quotes the name safely; a database name cannot be a %s parameter.
    if template is None:
        query = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
    else:
        query = sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            sql.Identifier(name), sql.Identifier(template)
        )
    # CREATE DATABASE cannot run inside a transaction, so this connection uses autocommit.
    with connect(autocommit=True) as conn:
        conn.execute(query)


def create_template_database():
    """Create the template database and its tables. Safe to run twice."""
    if not database_exists(TEMPLATE_DB):
        create_database(TEMPLATE_DB)
    # The connection must be closed before the template is copied,
    # because Postgres cannot copy a database that someone is connected to.
    with connect(TEMPLATE_DB) as conn:
        conn.execute(TENANT_SQL.read_text(encoding="utf-8"))


def create_tenant_database(tenant_id: str) -> str:
    """Copy the template into a new customer database. Safe to run twice."""
    name = database_name_for(tenant_id)
    if not database_exists(name):
        create_database(name, template=TEMPLATE_DB)

    with connect(name) as conn:
        conn.execute(
            """
            INSERT INTO tenant_meta (tenant_id, schema_version)
            VALUES (%s, %s)
            ON CONFLICT (tenant_id) DO NOTHING
            """,
            (tenant_id, SCHEMA_VERSION),
        )
    return name


def setup_tenant(tenant_id: str, name: str, username: str, password: str) -> str:
    """Everything a new customer needs: their database, and a row in tenants.

    This is a setup step, not part of a batch. Safe to run twice.
    """
    create_template_database()
    database_name = create_tenant_database(tenant_id)

    with connect() as conn:
        create_control_tables(conn)
        register_tenant(conn, tenant_id, name, username, hash_password(password), database_name)
    return database_name
