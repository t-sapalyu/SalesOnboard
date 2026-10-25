# SalesOnboard

Sales ETL Agent (Phase 1): turns customer sales exports into one canonical table.
See `dev_plan/SalesOnboard ETL Agent — System Design.md` and `dev_plan/DEVELOPMENT_PLAN.md`.

## Setup

```
uv sync
cp .env.sample .env   # then fill in your keys
docker compose up -d  # Postgres on localhost:5433
```

## Checks

```
uv run pytest
uv run ruff check
uv run ruff format --check
```
