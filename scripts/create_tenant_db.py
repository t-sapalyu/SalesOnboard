"""Set up one new customer: their own database, and their login.

Run from the project root, with Postgres running:
  uv run python -m scripts.create_tenant_db acme "Acme Ltd" acme_user

The password is asked for, so it never appears in your shell history.
"""

import argparse
import getpass

from salesonboard.db.tenant import setup_tenant


def main():
    parser = argparse.ArgumentParser(description="Set up a new customer.")
    parser.add_argument("tenant_id", help="short id, a-z 0-9 _ only, e.g. acme")
    parser.add_argument("name", help="company name shown on the page")
    parser.add_argument("username", help="login name")
    args = parser.parse_args()

    password = getpass.getpass("Password for the new login: ")
    database_name = setup_tenant(args.tenant_id, args.name, args.username, password)
    print(f"Tenant {args.tenant_id!r} ready, database {database_name!r}")


if __name__ == "__main__":
    main()
