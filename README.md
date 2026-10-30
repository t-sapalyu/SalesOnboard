# SalesOnboard

Sales ETL Agent (Phase 1): turns customer sales exports into one canonical table.

Every customer exports sales in their own format, with different column names,
date formats, decimal separators and delimiters. SalesOnboard loads these files
into one standard `sales_lines` table:

1. **Profile**: read the file and describe each column (type, empty values,
   date order, decimal separator).
2. **Plan**: an LLM agent writes a transform plan, which maps source columns
   to canonical fields and lists cleaning steps (trim, parse dates, cast numbers,
   drop empty rows). The agent only writes the plan. It never touches the data.
3. **Execute**: plain Python code applies the plan to every row.
4. **Validate**: rules check the result (required fields present, values parse,
   no negative prices, no rows lost). If any rule fails, nothing is loaded.
5. **Load**: rows are written in one transaction into the customer's own
   database. Each customer has a separate Postgres database, and a routing check
   stops data from going to the wrong one.

Current status: the minimum version works end to end with a hand-written plan
(no LLM yet). Postgres must be running and the demo tenants seeded (see Setup):

```
uv run python -m salesonboard.cli load company1 demo_data/company1/sales.csv --plan demo_data/company1/ground_truth.json
```

## Setup

```
uv sync
cp .env.sample .env   # then fill in your keys
docker compose up -d  # Postgres on localhost:5433
uv run python -m scripts.init_control_db
uv run python -m scripts.seed_tenants   # demo logins company1-3
```

## Checks

```
uv run pytest
uv run ruff check
uv run ruff format --check
```
