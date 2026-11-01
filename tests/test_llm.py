"""Tests for llm.py. They never call the real OpenAI API: a FakeClient returns a
prepared answer instead. Needs Postgres running: `docker compose up -d`.
"""

from types import SimpleNamespace

import pytest

from salesonboard.config import get_settings
from salesonboard.db import connect
from salesonboard.db.control import create_control_tables, register_tenant
from salesonboard.llm import (
    MAX_OUTPUT_TOKENS,
    CostLimitError,
    call_llm,
    estimate_max_cost,
    token_cost,
)

TENANT_ID = "pytest_llm"
BATCH_ID = "pytest-llm-batch"
MESSAGES = [{"role": "user", "content": "Say hello"}]


class FakeClient:
    """Stands in for openai.OpenAI. Remembers each request and returns a fixed
    answer with the token counts we choose, so costs are easy to check."""

    def __init__(self, input_tokens=1000, output_tokens=200, tool_calls=None):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.tool_calls = tool_calls
        self.requests = []
        # The real client is used as client.chat.completions.create(...)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        message = SimpleNamespace(role="assistant", content="hello", tool_calls=self.tool_calls)
        usage = SimpleNamespace(
            prompt_tokens=self.input_tokens, completion_tokens=self.output_tokens
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def fake_tool_call(name, arguments):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))


@pytest.fixture
def batch(db_conn):
    """A tenant and a batch, committed, because call_llm uses its own
    connections. Deleted again after the test."""
    with connect() as conn:
        create_control_tables(conn)
        register_tenant(conn, TENANT_ID, "Pytest LLM", "pytest_llm", "x", "tenant_pytest_llm")
        conn.execute(
            """
            INSERT INTO batches (batch_id, tenant_id, status, file_name, file_path, file_hash)
            VALUES (%s, %s, 'mapping', 'sales.csv', 'unused', 'unused')
            """,
            (BATCH_ID, TENANT_ID),
        )
    yield BATCH_ID
    with connect() as conn:
        conn.execute("DELETE FROM llm_calls WHERE batch_id = %s", (BATCH_ID,))
        conn.execute("DELETE FROM batches WHERE batch_id = %s", (BATCH_ID,))
        conn.execute("DELETE FROM tenants WHERE tenant_id = %s", (TENANT_ID,))


def set_batch_cost(batch_id, cost_eur):
    with connect() as conn:
        conn.execute("UPDATE batches SET cost_eur = %s WHERE batch_id = %s", (cost_eur, batch_id))


def logged_calls(batch_id):
    with connect() as conn:
        return conn.execute(
            """
            SELECT agent, model, input_tokens, output_tokens, cost_eur, tool_calls
            FROM llm_calls WHERE batch_id = %s
            """,
            (batch_id,),
        ).fetchall()


def batch_cost(batch_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT cost_eur FROM batches WHERE batch_id = %s", (batch_id,)
        ).fetchone()
    return float(row[0])


# --- Cost math (no database) ------------------------------------------------


def test_one_million_tokens_cost_the_configured_price():
    settings = get_settings()
    assert token_cost(1_000_000, 0) == pytest.approx(settings.price_input_per_mtok_eur)
    assert token_cost(0, 1_000_000) == pytest.approx(settings.price_output_per_mtok_eur)


def test_worst_case_includes_the_longest_answer():
    assert estimate_max_cost(MESSAGES, None) > token_cost(0, MAX_OUTPUT_TOKENS)


def test_longer_messages_have_a_higher_worst_case():
    long_messages = [{"role": "user", "content": "x" * 10_000}]
    assert estimate_max_cost(long_messages, None) > estimate_max_cost(MESSAGES, None)


# --- A normal call ----------------------------------------------------------


def test_returns_the_answer_and_its_cost(batch):
    client = FakeClient(input_tokens=1000, output_tokens=200)
    response = call_llm(batch, "mapping_agent", MESSAGES, client=client)

    assert response.message.content == "hello"
    assert response.input_tokens == 1000
    assert response.output_tokens == 200
    assert response.cost_eur == pytest.approx(token_cost(1000, 200))


def test_request_uses_model_from_config(batch):
    client = FakeClient()
    call_llm(batch, "mapping_agent", MESSAGES, client=client)

    request = client.requests[0]
    assert request["model"] == get_settings().openai_model
    assert request["messages"] == MESSAGES
    assert request["max_completion_tokens"] == MAX_OUTPUT_TOKENS
    assert "tools" not in request  # no tools given, so none sent


def test_tools_are_sent_when_given(batch):
    tools = [{"type": "function", "function": {"name": "get_profile", "parameters": {}}}]
    client = FakeClient()
    call_llm(batch, "mapping_agent", MESSAGES, tools=tools, client=client)
    assert client.requests[0]["tools"] == tools


def test_call_is_logged_and_added_to_batch_cost(batch):
    call_llm(batch, "mapping_agent", MESSAGES, client=FakeClient(1000, 200))
    call_llm(batch, "investigator", MESSAGES, client=FakeClient(500, 100))

    calls = logged_calls(batch)
    assert len(calls) == 2
    agent, model, input_tokens, output_tokens, cost_eur, tool_calls = calls[0]
    assert agent == "mapping_agent"
    assert model == get_settings().openai_model
    assert (input_tokens, output_tokens) == (1000, 200)
    assert float(cost_eur) == pytest.approx(token_cost(1000, 200), abs=1e-6)
    assert tool_calls == []

    expected_total = token_cost(1000, 200) + token_cost(500, 100)
    assert batch_cost(batch) == pytest.approx(expected_total, abs=1e-6)


def test_tool_calls_are_logged(batch):
    client = FakeClient(tool_calls=[fake_tool_call("get_profile", '{"column": "Date"}')])
    call_llm(batch, "mapping_agent", MESSAGES, client=client)

    tool_calls = logged_calls(batch)[0][5]
    assert tool_calls == [{"name": "get_profile", "arguments": '{"column": "Date"}'}]


# --- Cost limit -------------------------------------------------------------


def test_call_that_could_pass_the_limit_is_refused(batch):
    limit = get_settings().batch_cost_limit_eur
    set_batch_cost(batch, limit - 0.0001)  # almost everything already spent
    client = FakeClient()

    with pytest.raises(CostLimitError, match="passes"):
        call_llm(batch, "mapping_agent", MESSAGES, client=client)

    assert client.requests == []  # OpenAI was never called
    assert logged_calls(batch) == []


def test_unknown_batch_is_refused(db_conn):
    with pytest.raises(ValueError, match="Unknown batch"):
        call_llm("no-such-batch", "mapping_agent", MESSAGES, client=FakeClient())
