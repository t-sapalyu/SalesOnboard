"""Create the control database tables.

Run from the project root, with Postgres running (docker compose up -d):
  uv run python -m scripts.init_control_db
"""

from salesonboard.db import connect
from salesonboard.db.control import CONTROL_TABLES, create_control_tables


def main():
    # "with" commits when the block ends without an error, and rolls back if one happens.
    with connect() as conn:
        create_control_tables(conn)
    print("Control tables ready:", ", ".join(CONTROL_TABLES))


if __name__ == "__main__":
    main()
