"""Tests for the mapping agent. A ScriptedClient plays the model and answers
with the demo file's correct plan, so we test our code, not the LLM.
Needs Postgres running (every call is logged): `docker compose up -d`.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from salesonboard.agents.mapping import (
    MAX_SAMPLE_VALUES,
    run_mapping_agent,
    sample_values_tool,
)
from salesonboard.agents.tool_loop import InvalidOutputError
from salesonboard.pipeline.executor import execute
from salesonboard.pipeline.source_file import SourceFile, read_source_file
from salesonboard.pipeline.validator import all_passed, validate
from tests.fake_openai import ScriptedClient, answer, asks_for_tool, tool_results

COMPANY1 = Path("demo_data/company1")


@pytest.fixture
def source():
    return read_source_file(COMPANY1 / "sales.csv")


@pytest.fixture
def correct_plan_json():
    return (COMPANY1 / "ground_truth.json").read_text(encoding="utf-8")


def test_returns_the_plan_from_the_model(batch, source, correct_plan_json):
    client = ScriptedClient([answer(correct_plan_json)])
    plan = run_mapping_agent(batch, source, client=client)

    targets = {column.source: column.target for column in plan.columns}
    assert targets["OrderDate"] == "order_date"
    assert targets["SalesRep"] is None  # ignored column


def test_created_by_is_set_by_code(batch, source, correct_plan_json):
    # ground_truth.json says "human"; the agent's plan must say "mapping_agent".
    client = ScriptedClient([answer(correct_plan_json)])
    plan = run_mapping_agent(batch, source, client=client)
    assert {operation.created_by for operation in plan.operations} == {"mapping_agent"}


def test_first_message_has_fields_and_profile(batch, source, correct_plan_json):
    client = ScriptedClient([answer(correct_plan_json)])
    run_mapping_agent(batch, source, client=client)

    first_message = client.requests[0]["messages"][1]["content"]
    assert "order_date (date, required)" in first_message
    assert "store_id (text, optional)" in first_message
    assert "tenant_id" not in first_message  # set by the system, never mapped
    assert '"name": "OrderDate"' in first_message  # from the profile


def test_model_can_ask_for_sample_values(batch, source, correct_plan_json):
    client = ScriptedClient(
        [asks_for_tool("get_sample_values", {"column": "SKU", "n": 3}), answer(correct_plan_json)]
    )
    run_mapping_agent(batch, source, client=client)
    samples = json.loads(tool_results(client.requests[1])[0]["content"])
    assert len(samples) == 3


def test_plan_that_misses_a_column_is_rejected(batch, source, correct_plan_json):
    plan = json.loads(correct_plan_json)
    plan["columns"] = [column for column in plan["columns"] if column["source"] != "SalesRep"]
    client = ScriptedClient([answer(json.dumps(plan))])

    with pytest.raises(InvalidOutputError, match="SalesRep"):
        run_mapping_agent(batch, source, client=client)


def test_agent_plan_runs_through_executor_and_validator(batch, source, correct_plan_json):
    client = ScriptedClient([answer(correct_plan_json)])
    plan = run_mapping_agent(batch, source, client=client)

    result = execute(source.data, plan, tenant_id="company1", batch_id=batch)
    assert all_passed(validate(result))


# --- get_sample_values (no database) ----------------------------------------


def test_sample_values_are_distinct_and_keep_extra_spaces():
    data = pd.DataFrame({"SKU": ["A", "B ", "A", "", "B "]})
    get_sample_values = sample_values_tool(SourceFile(data=data, delimiter=",")).function
    assert get_sample_values("SKU") == ["A", "B ", ""]


def test_sample_values_are_capped(source):
    get_sample_values = sample_values_tool(source).function
    assert len(get_sample_values("OrderNo", n=500)) == MAX_SAMPLE_VALUES


def test_sample_values_for_unknown_column_fail(source):
    get_sample_values = sample_values_tool(source).function
    with pytest.raises(ValueError, match="No column"):
        get_sample_values("Nope")
