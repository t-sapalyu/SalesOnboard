"""Needs Postgres running: `docker compose up -d`. Skipped otherwise."""

import psycopg
import pytest

from salesonboard.db import connect


@pytest.fixture
def conn():
    try:
        connection = connect()
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running")
    with connection:
        yield connection


def test_connects_to_control_db(conn):
    assert conn.execute("SELECT current_database()").fetchone()[0] == "control"
