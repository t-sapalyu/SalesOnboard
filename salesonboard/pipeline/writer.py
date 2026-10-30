"""Load canonical rows into one customer's database.

This is the only code that writes sales data. Two safety rules:

1. Routing guard: before writing, read tenant_meta from the target database.
   If it belongs to another tenant, stop. A bug that picks the wrong database
   fails loudly instead of mixing two customers' data.

2. One transaction: every row is stored, or none is. If anything fails halfway,
   the database is left exactly as it was.
"""

import pandas as pd

from salesonboard import schema
from salesonboard.db import connect
from salesonboard.pipeline.executor import is_empty

# Column names come from schema.py, never from the uploaded file,
# so building the INSERT statement from them is safe.
COLUMNS = [field.name for field in schema.FIELDS]
COLUMN_LIST = ", ".join(COLUMNS)  # "tenant_id, batch_id, source_row, ..."
PLACEHOLDERS = ", ".join(["%s"] * len(COLUMNS))  # "%s, %s, %s, ..." one per column
INSERT_SQL = f"INSERT INTO sales_lines ({COLUMN_LIST}) VALUES ({PLACEHOLDERS})"


class RoutingError(Exception):
    """The target database does not belong to the batch's tenant."""


def write_rows(database_name: str, tenant_id: str, rows: pd.DataFrame) -> int:
    """Insert all rows into sales_lines. Returns how many rows were written."""
    check_rows_belong_to(tenant_id, rows)
    values = rows_to_values(rows)

    # "with connect(...)" is one transaction: it commits at the end of the
    # block, or rolls back everything if an error happens inside it.
    with connect(database_name) as conn:
        check_database_belongs_to(conn, tenant_id)
        with conn.cursor() as cursor:
            cursor.executemany(INSERT_SQL, values)
    return len(values)


def check_database_belongs_to(conn, tenant_id: str):
    rows = conn.execute("SELECT tenant_id FROM tenant_meta").fetchall()
    owners = [row[0] for row in rows]
    if owners != [tenant_id]:
        raise RoutingError(
            f"Database belongs to {owners}, not to {tenant_id!r}. Nothing was written."
        )


def check_rows_belong_to(tenant_id: str, rows: pd.DataFrame):
    other_tenants = set(rows["tenant_id"]) - {tenant_id}
    if other_tenants:
        raise RoutingError(f"Rows for {sorted(other_tenants)} cannot be written for {tenant_id!r}.")


def rows_to_values(rows: pd.DataFrame) -> list[tuple]:
    """Turn the DataFrame into plain Python tuples, in COLUMNS order.

    to_dict gives plain Python numbers instead of numpy numbers, which the
    database driver understands. Empty values (None, NaN, "") become NULL.
    """
    values = []
    for record in rows[COLUMNS].to_dict(orient="records"):
        row = []
        for column in COLUMNS:
            value = record[column]
            if is_empty(value):
                value = None
            row.append(value)
        values.append(tuple(row))
    return values
