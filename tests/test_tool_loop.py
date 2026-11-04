"""Tests for tool_loop.py. A ScriptedClient plays the model (see fake_openai.py).
Needs Postgres running (every call is logged): `docker compose up -d`.
"""

import pytest
from pydantic import BaseModel

from salesonboard.agents.tool_loop import (
    MAX_TOOL_CALLS,
    InvalidOutputError,
    Tool,
    ToolLimitError,
    run_agent,
    strip_code_fence,
)
from tests.fake_openai import ScriptedClient, answer, asks_for_tool, tool_results


class Greeting(BaseModel):
    """A tiny final answer shape for the tests."""

    text: str


def add(a, b):
    return {"sum": a + b}


def broken():
    raise ValueError("something went wrong")


ADD_TOOL = Tool(
    name="add",
    description="Add two numbers",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    },
    function=add,
)
BROKEN_TOOL = Tool("broken", "Always fails", {"type": "object", "properties": {}}, broken)


def run(batch, client, tools=None):
    return run_agent(
        batch_id=batch,
        agent="mapping_agent",
        system_prompt="You are a test agent.",
        user_message="Say hello",
        tools=tools or [ADD_TOOL],
        output_model=Greeting,
        client=client,
    )


# --- Final answer -----------------------------------------------------------


def test_final_answer_without_tools(batch):
    client = ScriptedClient([answer('{"text": "hello"}')])
    result = run(batch, client)
    assert result.output == Greeting(text="hello")
    assert result.tool_calls == 0


def test_system_prompt_includes_the_answer_schema(batch):
    client = ScriptedClient([answer('{"text": "hello"}')])
    run(batch, client)
    system_prompt = client.requests[0]["messages"][0]["content"]
    assert system_prompt.startswith("You are a test agent.")
    assert '"text"' in system_prompt  # the field from Greeting's schema


def test_tools_are_sent_in_openai_format(batch):
    client = ScriptedClient([answer('{"text": "hello"}')])
    run(batch, client)
    assert client.requests[0]["tools"] == [ADD_TOOL.definition()]
    assert client.requests[0]["tools"][0]["function"]["name"] == "add"


# --- Tool calls -------------------------------------------------------------


def test_tool_is_run_and_result_sent_back(batch):
    client = ScriptedClient([asks_for_tool("add", {"a": 2, "b": 3}), answer('{"text": "5"}')])
    result = run(batch, client)

    assert result.output.text == "5"
    assert result.tool_calls == 1

    second_request = client.requests[1]
    assistant_message = second_request["messages"][2]
    assert assistant_message["tool_calls"][0]["function"]["name"] == "add"
    assert tool_results(second_request) == [
        {"role": "tool", "tool_call_id": "call_1", "content": '{"sum": 5}'}
    ]


def test_unknown_tool_returns_an_error_to_the_model(batch):
    client = ScriptedClient([asks_for_tool("delete_all", {}), answer('{"text": "ok"}')])
    run(batch, client)
    content = tool_results(client.requests[1])[0]["content"]
    assert content == "Error: there is no tool called 'delete_all'."


def test_wrong_arguments_return_an_error_to_the_model(batch):
    client = ScriptedClient([asks_for_tool("add", {"a": 2}), answer('{"text": "ok"}')])
    run(batch, client)
    content = tool_results(client.requests[1])[0]["content"]
    assert content.startswith("Error in add:")


def test_error_inside_a_tool_returns_an_error_to_the_model(batch):
    client = ScriptedClient([asks_for_tool("broken", {}), answer('{"text": "ok"}')])
    run(batch, client, tools=[BROKEN_TOOL])
    content = tool_results(client.requests[1])[0]["content"]
    assert content == "Error in broken: something went wrong"


def test_too_many_tool_calls_stop_the_agent(batch):
    replies = [asks_for_tool("add", {"a": 1, "b": 1}) for _ in range(MAX_TOOL_CALLS + 1)]
    client = ScriptedClient(replies)
    with pytest.raises(ToolLimitError):
        run(batch, client)
    assert len(client.requests) == MAX_TOOL_CALLS + 1


# --- Invalid final answer ---------------------------------------------------


def test_invalid_answer_gets_one_retry(batch):
    client = ScriptedClient([answer("hello"), answer('{"text": "hello"}')])
    result = run(batch, client)

    assert result.output.text == "hello"
    retry_message = client.requests[1]["messages"][-1]
    assert retry_message["role"] == "user"
    assert "not valid" in retry_message["content"]


def test_invalid_answer_twice_stops_the_agent(batch):
    client = ScriptedClient([answer("hello"), answer('{"wrong": 1}')])
    with pytest.raises(InvalidOutputError):
        run(batch, client)


def test_answer_in_a_code_fence_is_accepted(batch):
    client = ScriptedClient([answer('```json\n{"text": "hello"}\n```')])
    assert run(batch, client).output.text == "hello"


# --- strip_code_fence (no database) -----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"text": "hi"}',
        '```json\n{"text": "hi"}\n```',
        '```\n{"text": "hi"}\n```',
        '  {"text": "hi"}  \n',
    ],
)
def test_strip_code_fence(text):
    assert strip_code_fence(text) == '{"text": "hi"}'
