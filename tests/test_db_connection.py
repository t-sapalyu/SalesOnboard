"""Needs Postgres running: `docker compose up -d`. Skipped otherwise."""


def test_connects_to_control_db(db_conn):
    assert db_conn.execute("SELECT current_database()").fetchone()[0] == "control"
