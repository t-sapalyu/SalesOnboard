"""Database access: plain SQL with psycopg."""

import psycopg

from salesonboard.config import get_settings


def connect(dbname: str | None = None) -> psycopg.Connection:
    """Open a connection to one database; the control database by default."""
    settings = get_settings()
    return psycopg.connect(settings.db_url(dbname or settings.control_db))
