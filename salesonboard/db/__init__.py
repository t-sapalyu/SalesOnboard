"""Database access: plain SQL with psycopg."""

import psycopg

from salesonboard.config import get_settings


def connect(dbname: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    """Open a connection to one database; the control database by default.

    autocommit=True is only needed for statements that cannot run inside a
    transaction, such as CREATE DATABASE.
    """
    settings = get_settings()
    url = settings.db_url(dbname or settings.control_db)
    return psycopg.connect(url, autocommit=autocommit)
