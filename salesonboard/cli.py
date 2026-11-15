"""Command line tool. Run from the project root, with Postgres running and the
demo tenants seeded.

Drive a batch step by step, like the web page will (needs OPENAI_API_KEY):
  uv run python -m salesonboard.cli start company1 demo_data/company1/sales.csv
  uv run python -m salesonboard.cli show <batch_id>
  uv run python -m salesonboard.cli answer <batch_id> Store=store_id SalesRep=skip
  uv run python -m salesonboard.cli approve <batch_id>
  uv run python -m salesonboard.cli support <batch_id> --note "Prices look wrong"
  uv run python -m salesonboard.cli stop <batch_id>

Or load a file directly with a plan you wrote, no LLM and no audit trail
(running the same file twice loads it twice):
  uv run python -m salesonboard.cli load company1 demo_data/company1/sales.csv \
      --plan demo_data/company1/ground_truth.json
"""

import argparse
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from salesonboard.db import connect
from salesonboard.db.control import (
    get_batch,
    get_latest_plan_version,
    get_latest_validation_results,
    get_support_tickets,
    get_tenant,
)
from salesonboard.pipeline.executor import execute
from salesonboard.pipeline.source_file import read_source_file
from salesonboard.pipeline.validator import RuleResult, all_passed, validate
from salesonboard.pipeline.writer import write_rows
from salesonboard.transform_plan import TransformPlan
from salesonboard.workflow import batch_steps
from salesonboard.workflow.support_tickets import REASONS, send_to_support

# What the customer can do next, per status.
NEXT_ACTION = {
    "questions": "answer <batch_id> Column=field (or Column=skip)",
    "review_mapping": "approve <batch_id> to use this plan, or stop <batch_id>",
    "fix_problems": "approve <batch_id> to apply the fix, or stop <batch_id>",
    "summary": "approve <batch_id> to load the data, or stop <batch_id>",
}
# At these statuses the customer can also press "Send to support".
NEXT_ACTION_FOOTER = '(or: support <batch_id> --note "what looks wrong")'


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


# --- Batch workflow commands --------------------------------------------------


def start(tenant_id: str, file_path: Path, client=None) -> str:
    """Create a batch and let the mapping agent write the first plan."""
    batch_id = batch_steps.create_batch(tenant_id, file_path)
    batch_steps.run_mapping(batch_id, client=client)
    return batch_id


def approve(batch_id: str, client=None):
    """Press the button the batch is waiting for, then run the slow step after it."""
    with connect() as conn:
        status = get_batch(conn, batch_id)["status"]

    if status == "review_mapping":
        batch_steps.approve_mapping(batch_id)
        batch_steps.run_validation(batch_id, client=client)
    elif status == "fix_problems":
        batch_steps.apply_fix(batch_id)
        batch_steps.run_validation(batch_id, client=client)
    elif status == "summary":
        batch_steps.approve_summary(batch_id)
        batch_steps.run_load(batch_id)
    else:
        raise ValueError(f"Batch {batch_id} is at {status!r}; there is nothing to approve.")


def parse_answers(pairs: list[str]) -> dict[str, str | None]:
    """["Store=store_id", "Notes=skip"] -> {"Store": "store_id", "Notes": None}"""
    answers = {}
    for pair in pairs:
        column, _, target = pair.partition("=")
        answers[column] = None if target == "skip" else target
    return answers


def show(batch_id: str):
    """Print where the batch is, its latest plan and its latest validation run."""
    with connect() as conn:
        batch = get_batch(conn, batch_id)
        version = get_latest_plan_version(conn, batch_id)
        results = get_latest_validation_results(conn, batch_id)
        tickets = get_support_tickets(conn, batch_id)
    if batch is None:
        raise ValueError(f"Unknown batch {batch_id!r}")

    print(f"Batch {batch_id} ({batch['tenant_id']}, {batch['file_name']})")
    print(f"  status: {batch['status']}   LLM cost so far: €{float(batch['cost_eur']):.4f}")

    if version is not None:
        print(f"Plan version {version['version']} ({version['action']} by {version['created_by']})")
        if version["note"]:
            print(f"  note: {version['note']}")
        for column in version["plan"]["columns"]:
            if column["status"] == "mapped":
                print(f"  {column['source']:<20} -> {column['target']}")
            else:
                print(f"  {column['source']:<20} {column['status']}: {column['reason']}")

    if results:
        print(f"Validation run {results[0]['run']}:")
        for result in results:
            status = "pass" if result["passed"] else f"FAIL ({len(result['failing_rows'])} rows)"
            print(f"  {result['rule']}  {status}")

    if batch["status"] == "loaded":
        print(f"Loaded {batch['rows_loaded']} rows ({batch['rows_dropped']} dropped).")
    for ticket in tickets:
        reason = REASONS[ticket["reason"]]
        print(f"Support ticket #{ticket['ticket_id']} ({ticket['status']}): {reason}")
        if ticket["note"]:
            print(f"  note: {ticket['note']}")
    if batch["status"] in NEXT_ACTION:
        print(f"Next: {NEXT_ACTION[batch['status']]}")
        print(f"      {NEXT_ACTION_FOOTER}")


def main(argv: list[str] | None = None) -> int:
    """Returns the exit code: 0 when it worked, 1 when it did not."""
    parser = argparse.ArgumentParser(prog="salesonboard")
    commands = parser.add_subparsers(dest="command", required=True)

    load = commands.add_parser("load", help="load a sales file with a given plan")
    load.add_argument("tenant_id", help="e.g. company1")
    load.add_argument("file", type=Path, help="the sales CSV file")
    load.add_argument("--plan", type=Path, required=True, help="the plan JSON file")

    start_command = commands.add_parser("start", help="upload a file and run the mapping agent")
    start_command.add_argument("tenant_id", help="e.g. company1")
    start_command.add_argument("file", type=Path, help="the sales CSV file")

    for name, help_text in [
        ("show", "show a batch's status, plan and validation"),
        ("approve", "press the button the batch is waiting for"),
        ("stop", "give up on this batch"),
    ]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("batch_id")

    support_command = commands.add_parser("support", help="send this batch to the support team")
    support_command.add_argument("batch_id")
    support_command.add_argument("--note", help="what looks wrong (optional)")

    answer_command = commands.add_parser("answer", help="answer the questions on unclear columns")
    answer_command.add_argument("batch_id")
    answer_command.add_argument("answers", nargs="+", help="Column=field, or Column=skip")

    args = parser.parse_args(argv)

    if args.command == "load":
        summary = load_file(args.tenant_id, args.file, args.plan)
        print_summary(summary)
        return 0 if summary.loaded else 1

    if args.command == "start":
        batch_id = start(args.tenant_id, args.file)
    else:
        batch_id = args.batch_id
        if args.command == "approve":
            approve(batch_id)
        elif args.command == "answer":
            batch_steps.answer_questions(batch_id, parse_answers(args.answers))
        elif args.command == "support":
            send_to_support(batch_id, "customer", note=args.note)
        elif args.command == "stop":
            batch_steps.stop_batch(batch_id)
    show(batch_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
