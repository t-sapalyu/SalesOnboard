"""The batch workflow: one function per step of an upload's journey.

Each step:
1. checks the batch is at the right status,
2. does its one job (run an agent, validate, load ...),
3. saves the result and the new status together, in one transaction.

All state lives in the control database (batches, plan_versions,
validation_results), never in memory. So any step can run in a background
task, and a batch can continue after a restart.

The plan to run is always the batch's latest plan version.

    create_batch      -> mapping
    run_mapping       mapping        -> questions or review_mapping
    answer_questions  questions      -> review_mapping
    approve_mapping   review_mapping -> validating
    run_validation    validating     -> summary, fix_problems or support
    apply_fix         fix_problems   -> validating
    approve_summary   summary        -> loading
    run_load          loading        -> loaded
    stop_batch        (any active)   -> stopped

When the system cannot continue on its own (an agent error such as the cost
limit, or the investigator's round limit), the batch goes to "support" with a
ticket. See support_tickets.py.
"""

import hashlib
import uuid
from pathlib import Path

from salesonboard.agents.investigator import run_investigator
from salesonboard.agents.mapping import run_mapping_agent
from salesonboard.db import connect
from salesonboard.db.control import (
    get_batch,
    get_latest_plan_version,
    get_tenant,
    insert_batch,
    save_plan_version,
    save_validation_results,
    update_batch,
)
from salesonboard.pipeline.executor import execute
from salesonboard.pipeline.source_file import read_source_file
from salesonboard.pipeline.validator import all_passed, validate
from salesonboard.pipeline.writer import write_rows
from salesonboard.transform_plan import ColumnDecision, MapColumn, TransformPlan
from salesonboard.workflow.batch_status import FIRST_STATUS, InvalidMoveError, check_move
from salesonboard.workflow.support_tickets import (
    AGENT_ERRORS,
    reason_for_agent_error,
    send_to_support,
)

MAX_INVESTIGATOR_ROUNDS = 2


# --- Steps ------------------------------------------------------------------


def create_batch(tenant_id: str, file_path: Path) -> str:
    """Register an uploaded file as a new batch. Returns the batch id.

    The database refuses a second active batch for the same customer
    (the one_active_batch_per_tenant index).
    """
    source = read_source_file(file_path)
    batch_id = uuid.uuid4().hex[:12]
    with connect() as conn:
        if get_tenant(conn, tenant_id) is None:
            raise ValueError(f"Unknown tenant {tenant_id!r}")
        insert_batch(
            conn,
            batch_id=batch_id,
            tenant_id=tenant_id,
            status=FIRST_STATUS,
            file_name=file_path.name,
            file_path=str(file_path),
            file_hash=file_hash(file_path),
            row_count=len(source.data),
        )
    return batch_id


def run_mapping(batch_id: str, client=None) -> str:
    """The mapping agent writes the first plan. Returns the new status."""
    batch = read_batch(batch_id, expected_status="mapping")
    source = read_source_file(Path(batch["file_path"]))

    try:
        plan = run_mapping_agent(batch_id, source, client=client)
    except AGENT_ERRORS as error:
        send_to_support(batch_id, reason_for_agent_error(error), note=str(error))
        return "support"

    new_status = "questions" if has_questions(plan) else "review_mapping"
    with connect() as conn:
        move_batch(conn, batch_id, "mapping", new_status)
        save_plan_version(conn, batch_id, plan_to_json(plan), "mapping_agent", "proposed")
    return new_status


def answer_questions(batch_id: str, answers: dict[str, str | None]) -> str:
    """The customer's answers for the ambiguous columns.

    answers maps each ambiguous column to a canonical field, or to None to
    skip the column. Example: {"Store": "store_id"}
    """
    with connect() as conn:
        move_batch(conn, batch_id, "questions", "review_mapping")
        plan = latest_plan(conn, batch_id)
        new_plan = apply_answers(plan, answers)
        save_plan_version(conn, batch_id, plan_to_json(new_plan), "human", "answered")
    return "review_mapping"


def approve_mapping(batch_id: str) -> str:
    """The customer pressed "Use this plan"."""
    with connect() as conn:
        move_batch(conn, batch_id, "review_mapping", "validating")
        version = get_latest_plan_version(conn, batch_id)
        save_plan_version(conn, batch_id, version["plan"], "human", "approved")
    return "validating"


def run_validation(batch_id: str, client=None) -> str:
    """Run the plan and check the rules. If a rule fails, the investigator
    looks for a fix (at most MAX_INVESTIGATOR_ROUNDS times per batch).

    Returns the new status: summary, fix_problems or support.
    """
    batch = read_batch(batch_id, expected_status="validating")
    source = read_source_file(Path(batch["file_path"]))
    with connect() as conn:
        plan = latest_plan(conn, batch_id)

    result = execute(source.data, plan, tenant_id=batch["tenant_id"], batch_id=batch_id)
    rule_results = validate(result)
    with connect() as conn:
        save_validation_results(conn, batch_id, rule_results)

    if all_passed(rule_results):
        with connect() as conn:
            move_batch(conn, batch_id, "validating", "summary")
            update_batch(conn, batch_id, rows_dropped=len(result.dropped))
        return "summary"

    if batch["investigator_rounds"] >= MAX_INVESTIGATOR_ROUNDS:
        send_to_support(batch_id, "round_limit")
        return "support"

    # Count the round before the agent runs, so a crash still counts.
    with connect() as conn:
        update_batch(conn, batch_id, investigator_rounds=batch["investigator_rounds"] + 1)

    try:
        proposal = run_investigator(batch_id, batch["tenant_id"], source, plan, client=client)
    except AGENT_ERRORS as error:
        send_to_support(batch_id, reason_for_agent_error(error), note=str(error))
        return "support"

    if proposal.new_plan is None:  # no fix found
        send_to_support(batch_id, "no_fix", note=proposal.diagnosis)
        return "support"

    with connect() as conn:
        move_batch(conn, batch_id, "validating", "fix_problems")
        save_plan_version(
            conn,
            batch_id,
            plan_to_json(proposal.new_plan),
            "investigator",
            "fix_proposed",
            note=proposal.diagnosis,
        )
    return "fix_problems"


def apply_fix(batch_id: str) -> str:
    """The customer pressed "Apply fix". The fixed plan is validated again."""
    with connect() as conn:
        move_batch(conn, batch_id, "fix_problems", "validating")
        version = get_latest_plan_version(conn, batch_id)
        save_plan_version(conn, batch_id, version["plan"], "human", "fix_applied")
    return "validating"


def approve_summary(batch_id: str) -> str:
    """The customer pressed "Load my data"."""
    with connect() as conn:
        move_batch(conn, batch_id, "summary", "loading")
    return "loading"


def run_load(batch_id: str) -> str:
    """Write the rows into the customer's own database."""
    batch = read_batch(batch_id, expected_status="loading")
    source = read_source_file(Path(batch["file_path"]))
    with connect() as conn:
        tenant = get_tenant(conn, batch["tenant_id"])
        plan = latest_plan(conn, batch_id)

    # Run the approved plan again. It is deterministic, so it gives the same
    # rows the customer saw in the summary; the check is only a safety net.
    result = execute(source.data, plan, tenant_id=batch["tenant_id"], batch_id=batch_id)
    if not all_passed(validate(result)):
        raise RuntimeError(f"Batch {batch_id}: the approved plan no longer passes validation")

    rows_loaded = write_rows(tenant["database_name"], batch["tenant_id"], result.rows)

    # The rows are now committed in the customer's database. If the program
    # stopped right here, the batch would stay "loading"; running run_load
    # again fails on UNIQUE (batch_id, source_row), so rows are never stored twice.
    with connect() as conn:
        move_batch(conn, batch_id, "loading", "loaded")
        update_batch(conn, batch_id, rows_loaded=rows_loaded, rows_dropped=len(result.dropped))
    return "loaded"


def stop_batch(batch_id: str) -> str:
    """The customer gave up on this upload. They can upload a new file after."""
    with connect() as conn:
        batch = get_batch(conn, batch_id, lock=True)
        if batch is None:
            raise ValueError(f"Unknown batch {batch_id!r}")
        move_batch(conn, batch_id, batch["status"], "stopped")
    return "stopped"


# --- Helpers ----------------------------------------------------------------


def read_batch(batch_id: str, expected_status: str) -> dict:
    """The batch, after checking it is at the status this step needs.

    Checked before slow work starts, so an agent never runs (and costs money)
    for a batch that is at another step.
    """
    with connect() as conn:
        batch = get_batch(conn, batch_id)
    if batch is None:
        raise ValueError(f"Unknown batch {batch_id!r}")
    if batch["status"] != expected_status:
        raise InvalidMoveError(
            f"Batch {batch_id} is at {batch['status']!r}; this step needs {expected_status!r}."
        )
    return batch


def move_batch(conn, batch_id: str, expected_status: str, new_status: str) -> dict:
    """Change the status, after checking the batch is at expected_status and
    the move is allowed. The row stays locked until the transaction ends, so
    two requests cannot move the same batch at the same time."""
    batch = get_batch(conn, batch_id, lock=True)
    if batch is None:
        raise ValueError(f"Unknown batch {batch_id!r}")
    if batch["status"] != expected_status:
        raise InvalidMoveError(
            f"Batch {batch_id} is at {batch['status']!r}, not {expected_status!r}."
        )
    check_move(expected_status, new_status)
    update_batch(conn, batch_id, status=new_status)
    return batch


def latest_plan(conn, batch_id: str) -> TransformPlan:
    version = get_latest_plan_version(conn, batch_id)
    if version is None:
        raise ValueError(f"Batch {batch_id} has no plan yet")
    return TransformPlan.model_validate(version["plan"])


def plan_to_json(plan: TransformPlan) -> dict:
    # mode="json" gives plain JSON types, ready for the jsonb column.
    return plan.model_dump(mode="json")


def has_questions(plan: TransformPlan) -> bool:
    for column in plan.columns:
        if column.status == "ambiguous":
            return True
    return False


def apply_answers(plan: TransformPlan, answers: dict[str, str | None]) -> TransformPlan:
    """A new plan where every ambiguous column is mapped or ignored.

    A mapped answer adds a map_column operation. If the field also needs
    cleaning (a date or number format), validation fails on it and the
    investigator proposes the missing step.
    """
    ambiguous = [column.source for column in plan.columns if column.status == "ambiguous"]
    if sorted(answers) != sorted(ambiguous):
        raise ValueError(f"Answer exactly the unclear columns: {ambiguous}")

    columns = []
    operations = list(plan.operations)
    for column in plan.columns:
        if column.status != "ambiguous":
            columns.append(column)
            continue

        target = answers[column.source]
        if target is None:
            columns.append(
                ColumnDecision(
                    source=column.source, status="ignored", reason="The customer skipped it"
                )
            )
        else:
            columns.append(ColumnDecision(source=column.source, status="mapped", target=target))
            operations.append(
                MapColumn(
                    id=next_operation_id(operations),
                    op="map_column",
                    reason="The customer's answer",
                    created_by="human",
                    source=column.source,
                    target=target,
                )
            )
    # Building a new TransformPlan runs all its checks, e.g. a valid target field.
    return TransformPlan(columns=columns, operations=operations)


def next_operation_id(operations: list) -> str:
    used = {operation.id for operation in operations}
    number = len(operations) + 1
    while f"op{number}" in used:
        number += 1
    return f"op{number}"


def file_hash(path: Path) -> str:
    """sha256 of the file, to prove later which exact file was loaded."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
