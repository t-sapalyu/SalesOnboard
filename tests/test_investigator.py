"""Tests for the investigator. The broken plan is company2's correct plan with
the wrong date order (%m/%d/%Y), so R2 fails, like a real investigator case.
A ScriptedClient plays the model. Needs Postgres running: `docker compose up -d`.
"""

import json
from pathlib import Path

import pytest

from salesonboard.agents.investigator import (
    PlanChange,
    apply_change,
    failed_rows_tool,
    run_investigator,
)
from salesonboard.agents.tool_loop import InvalidOutputError
from salesonboard.pipeline.executor import execute
from salesonboard.pipeline.source_file import read_source_file
from salesonboard.pipeline.validator import all_passed, validate
from salesonboard.transform_plan import ParseDate, TransformPlan, Trim
from tests.fake_openai import ScriptedClient, answer, asks_for_tool, tool_results

COMPANY2 = Path("demo_data/company2")

# op7 is the parse_date operation in company2's plan.
FIXED_DATE = {
    "id": "op7",
    "op": "parse_date",
    "reason": "day comes first",
    "created_by": "investigator",
    "column": "order_date",
    "format": "%d/%m/%Y",
}


@pytest.fixture
def source():
    return read_source_file(COMPANY2 / "sales.csv")


@pytest.fixture
def broken_plan():
    plan = json.loads((COMPANY2 / "ground_truth.json").read_text(encoding="utf-8"))
    for operation in plan["operations"]:
        if operation["op"] == "parse_date":
            operation["format"] = "%m/%d/%Y"
    return TransformPlan.model_validate(plan)


def final_answer(operations, diagnosis="Dates are day first."):
    return answer(json.dumps({"diagnosis": diagnosis, "change": {"operations": operations}}))


def run(batch, source, plan, replies):
    client = ScriptedClient(replies)
    proposal = run_investigator(batch, "company2", source, plan, client=client)
    return proposal, client


# --- The agent run ----------------------------------------------------------


def test_simulated_fix_is_proposed_with_its_result(batch, source, broken_plan):
    proposal, client = run(
        batch,
        source,
        broken_plan,
        [
            asks_for_tool("simulate_plan_change", {"operations": [FIXED_DATE]}),
            final_answer([FIXED_DATE]),
        ],
    )

    assert proposal.diagnosis == "Dates are day first."
    assert all_passed(proposal.simulated_results)

    # The simulation result was sent back to the model too.
    report = json.loads(tool_results(client.requests[1])[0]["content"])
    assert all(rule["passed"] for rule in report)

    # The new plan really works.
    result = execute(source.data, proposal.new_plan, tenant_id="company2", batch_id=batch)
    assert all_passed(validate(result))


def test_first_message_has_plan_and_failing_rule(batch, source, broken_plan):
    _, client = run(batch, source, broken_plan, [final_answer([])])
    first_message = client.requests[0]["messages"][1]["content"]
    assert "%m/%d/%Y" in first_message  # the current plan
    assert '"rule": "R2"' in first_message  # the validation report
    assert '"passed": false' in first_message


def test_change_that_was_not_simulated_is_rejected(batch, source, broken_plan):
    with pytest.raises(InvalidOutputError, match="did not simulate"):
        run(batch, source, broken_plan, [final_answer([FIXED_DATE])])


def test_simulated_change_must_match_exactly(batch, source, broken_plan):
    other_format = {**FIXED_DATE, "format": "%d-%m-%Y"}
    replies = [
        asks_for_tool("simulate_plan_change", {"operations": [other_format]}),
        final_answer([FIXED_DATE]),
    ]
    with pytest.raises(InvalidOutputError):
        run(batch, source, broken_plan, replies)


def test_created_by_is_set_by_code(batch, source, broken_plan):
    from_model = {**FIXED_DATE, "created_by": "human"}
    proposal, _ = run(
        batch,
        source,
        broken_plan,
        [
            asks_for_tool("simulate_plan_change", {"operations": [from_model]}),
            final_answer([from_model]),
        ],
    )
    assert proposal.change.operations[0].created_by == "investigator"


def test_no_fix_found(batch, source, broken_plan):
    proposal, _ = run(batch, source, broken_plan, [final_answer([], "I could not find the cause.")])
    assert proposal.new_plan is None
    assert proposal.simulated_results == []


# --- get_failed_rows (no database) ------------------------------------------


def test_failed_rows_show_raw_next_to_transformed(source, broken_plan):
    result = execute(source.data, broken_plan, tenant_id="company2", batch_id="b1")
    rule_results = validate(result)
    get_failed_rows = failed_rows_tool(source, result, rule_results).function

    rows = get_failed_rows("R2", n=2)
    assert len(rows) == 2
    first = rows[0]
    assert "/" in first["raw"]["Date"]  # e.g. 25/01/2025 as written in the file
    assert first["transformed"]["order_date"] is None  # could not be parsed month first


def test_failed_rows_for_unknown_rule_fail(source, broken_plan):
    result = execute(source.data, broken_plan, tenant_id="company2", batch_id="b1")
    get_failed_rows = failed_rows_tool(source, result, validate(result)).function
    with pytest.raises(ValueError, match="No rule"):
        get_failed_rows("R99")


# --- apply_change (no database) ---------------------------------------------


def test_same_id_replaces_the_operation(broken_plan):
    change = PlanChange(operations=[ParseDate(**FIXED_DATE)])
    new_plan = apply_change(broken_plan, change)

    parse_dates = [op for op in new_plan.operations if op.op == "parse_date"]
    assert len(parse_dates) == 1
    assert parse_dates[0].format == "%d/%m/%Y"
    assert len(new_plan.operations) == len(broken_plan.operations)


def test_new_id_is_added_at_the_end(broken_plan):
    trim = Trim(
        id="op10", op="trim", reason="spaces", created_by="investigator", column="product_id"
    )
    new_plan = apply_change(broken_plan, PlanChange(operations=[trim]))
    assert new_plan.operations[-1] == trim


def test_old_plan_is_not_modified(broken_plan):
    apply_change(broken_plan, PlanChange(operations=[ParseDate(**FIXED_DATE)]))
    parse_date = [op for op in broken_plan.operations if op.op == "parse_date"][0]
    assert parse_date.format == "%m/%d/%Y"
