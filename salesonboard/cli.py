"""Command line tool: load one sales file into a customer's database, using a
plan you give it. No LLM and no web page; this connects the pipeline pieces.

Run from the project root, with Postgres running and the demo tenants seeded:
  uv run python -m salesonboard.cli load company1 demo_data/company1/sales.csv \
      --plan demo_data/company1/ground_truth.json

Note: there is no audit trail yet (batches, plan_versions ...); the batch
workflow adds that in A5. Running the same file twice loads it twice.
"""

import argparse
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from salesonboard.db import connect
from salesonboard.db.control import get_tenant
from salesonboard.pipeline.executor import execute
from salesonboard.pipeline.source_file import read_source_file
from salesonboard.pipeline.validator import RuleResult, all_passed, validate
from salesonboard.pipeline.writer import write_rows
from salesonboard.transform_plan import TransformPlan


@dataclass
class LoadSummary:
    batch_id: str
    database_name: str
    rows_in: int
    rows_dropped: int
    rows_loaded: int  # 0 when a rule failed
    rule_results: list[RuleResult]
    loaded: bool


def read_plan(path: Path) -> TransformPlan:
    return TransformPlan.model_validate(json.loads(path.read_text(encoding="utf-8")))


def new_batch_id() -> str:
    return f"cli-{uuid.uuid4().hex[:8]}"


def load_file(tenant_id: str, file_path: Path, plan_path: Path) -> LoadSummary:
    """Read, execute, validate, and write only if every rule passed."""
    with connect() as conn:
        tenant = get_tenant(conn, tenant_id)
    if tenant is None:
        raise ValueError(
            f"Unknown tenant {tenant_id!r}. Run: uv run python -m scripts.seed_tenants"
        )

    source = read_source_file(file_path)
    plan = read_plan(plan_path)
    batch_id = new_batch_id()

    result = execute(source.data, plan, tenant_id=tenant_id, batch_id=batch_id)
    rule_results = validate(result)

    rows_loaded = 0
    if all_passed(rule_results):
        rows_loaded = write_rows(tenant["database_name"], tenant_id, result.rows)

    return LoadSummary(
        batch_id=batch_id,
        database_name=tenant["database_name"],
        rows_in=result.rows_in,
        rows_dropped=len(result.dropped),
        rows_loaded=rows_loaded,
        rule_results=rule_results,
        loaded=all_passed(rule_results),
    )


def print_summary(summary: LoadSummary):
    print(f"Batch {summary.batch_id}: {summary.rows_in} rows in the file")
    for rule in summary.rule_results:
        status = "pass" if rule.passed else "FAIL"
        line = f"  {rule.rule}  {rule.check:<45} {status}"
        if rule.message:
            line += f"  ({rule.message})"
        print(line)
        if not rule.passed and rule.failing_rows:
            first_rows = ", ".join(str(row) for row in rule.failing_rows[:10])
            print(f"        failing rows: {first_rows} ({len(rule.failing_rows)} in total)")

    if summary.loaded:
        print(
            f"Loaded {summary.rows_loaded} rows into {summary.database_name} "
            f"({summary.rows_dropped} dropped)"
        )
    else:
        print("Nothing was loaded: fix the plan and run again.")


def main(argv: list[str] | None = None) -> int:
    """Returns the exit code: 0 when the file was loaded, 1 when it was not."""
    parser = argparse.ArgumentParser(prog="salesonboard")
    commands = parser.add_subparsers(dest="command", required=True)

    load = commands.add_parser("load", help="load a sales file with a given plan")
    load.add_argument("tenant_id", help="e.g. company1")
    load.add_argument("file", type=Path, help="the sales CSV file")
    load.add_argument("--plan", type=Path, required=True, help="the plan JSON file")

    args = parser.parse_args(argv)

    if args.command == "load":
        summary = load_file(args.tenant_id, args.file, args.plan)
        print_summary(summary)
        return 0 if summary.loaded else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
