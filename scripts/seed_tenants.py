"""Set up the three demo customers: company1, company2 and company3.

Run from the project root, with Postgres running:
  uv run python -m scripts.seed_tenants

Safe to run more than once. The passwords below are for local demos only;
never put real passwords in code.
"""

from salesonboard.db.tenant import setup_tenant

DEMO_TENANTS = [
    {"tenant_id": "company1", "name": "Company 1 (ERP export)", "password": "demo-company1"},
    {"tenant_id": "company2", "name": "Company 2 (French shop)", "password": "demo-company2"},
    {"tenant_id": "company3", "name": "Company 3 (UK point of sale)", "password": "demo-company3"},
]


def main():
    for tenant in DEMO_TENANTS:
        database_name = setup_tenant(
            tenant_id=tenant["tenant_id"],
            name=tenant["name"],
            username=tenant["tenant_id"],  # the login name is the tenant id
            password=tenant["password"],
        )
        print(f"{tenant['tenant_id']}: database {database_name}")


if __name__ == "__main__":
    main()
