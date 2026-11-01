"""The only place that calls OpenAI. Every LLM call in the project goes through
call_llm(), which does four things:

1. Budget check: refuse the call if it could push the batch past the cost
   limit (€1 by default). The caller stops the batch and opens a support ticket.
2. Call OpenAI. The SDK retries rate-limit and server errors by itself.
3. Compute the cost from the token counts in the response.
4. Log the call in llm_calls and add its cost to batches.cost_eur.

Logging uses its own connection and commits straight away, so a call's cost
is saved even if the step that made the call fails later.
"""

import json
import time
from dataclasses import dataclass

from openai import OpenAI
from psycopg.types.json import Jsonb

from salesonboard.config import get_settings
from salesonboard.db import connect

# The longest answer we allow. Also used for the worst-case cost of a call.
MAX_OUTPUT_TOKENS = 4000

# Rough rule for English text and JSON: one token is about 4 characters.
# Only used for the budget check before a call; the real cost comes from
# the token counts that OpenAI sends back.
CHARACTERS_PER_TOKEN = 4

# How many times the SDK retries a rate-limit (429) or server (5xx) error.
MAX_RETRIES = 3


class CostLimitError(Exception):
    """The next call could push the batch past its cost limit."""


@dataclass
class LlmResponse:
    message: object  # the assistant message: .content (text) and .tool_calls
    input_tokens: int
    output_tokens: int
    cost_eur: float
    latency_ms: int


def call_llm(
    batch_id: str,
    agent: str,
    messages: list,
    tools: list | None = None,
    client: OpenAI | None = None,
) -> LlmResponse:
    """Send messages to the model and return its answer.

    agent is "mapping_agent" or "investigator", for the log.
    tools is the list of tool definitions (JSON schema), or None.
    client is only passed by tests, to use a fake instead of the real API.
    """
    settings = get_settings()

    spent = cost_so_far(batch_id)
    worst_case = estimate_max_cost(messages, tools)
    if spent + worst_case > settings.batch_cost_limit_eur:
        raise CostLimitError(
            f"Batch {batch_id} has spent €{spent:.4f}; the next call could cost up to "
            f"€{worst_case:.4f}, which passes the €{settings.batch_cost_limit_eur} limit."
        )

    if client is None:
        client = make_client()

    request = {
        "model": settings.openai_model,
        "messages": messages,
        "max_completion_tokens": MAX_OUTPUT_TOKENS,
    }
    if tools:
        request["tools"] = tools

    start = time.perf_counter()
    response = client.chat.completions.create(**request)
    latency_ms = round((time.perf_counter() - start) * 1000)

    message = response.choices[0].message
    input_tokens = response.usage.prompt_tokens
    output_tokens = response.usage.completion_tokens
    cost_eur = token_cost(input_tokens, output_tokens)

    log_call(
        batch_id=batch_id,
        agent=agent,
        model=settings.openai_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_eur=cost_eur,
        latency_ms=latency_ms,
        tool_calls=tool_calls_for_log(message),
    )
    return LlmResponse(message, input_tokens, output_tokens, cost_eur, latency_ms)


def make_client() -> OpenAI:
    api_key = get_settings().openai_api_key.get_secret_value()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set. Add it to your .env file.")
    return OpenAI(api_key=api_key, max_retries=MAX_RETRIES)


def token_cost(input_tokens: int, output_tokens: int) -> float:
    """Euros for one call. Prices in config are per 1 million tokens."""
    settings = get_settings()
    input_cost = input_tokens * settings.price_input_per_mtok_eur / 1_000_000
    output_cost = output_tokens * settings.price_output_per_mtok_eur / 1_000_000
    return input_cost + output_cost


def estimate_max_cost(messages: list, tools: list | None) -> float:
    """The most the next call could cost: estimated input + the longest answer."""
    # default=str turns objects that are not plain JSON (like an assistant
    # message from an earlier call) into text; close enough for an estimate.
    text = json.dumps([messages, tools], default=str)
    estimated_input_tokens = len(text) // CHARACTERS_PER_TOKEN
    return token_cost(estimated_input_tokens, MAX_OUTPUT_TOKENS)


def tool_calls_for_log(message) -> list[dict]:
    """Names and arguments of the tools the model asked for, if any."""
    calls = []
    for tool_call in message.tool_calls or []:
        calls.append({"name": tool_call.function.name, "arguments": tool_call.function.arguments})
    return calls


def cost_so_far(batch_id: str) -> float:
    with connect() as conn:
        row = conn.execute(
            "SELECT cost_eur FROM batches WHERE batch_id = %s", (batch_id,)
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown batch {batch_id!r}")
    return float(row[0])


def log_call(
    batch_id: str,
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_eur: float,
    latency_ms: int,
    tool_calls: list[dict],
):
    """Save the call and add its cost to the batch, in one transaction."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO llm_calls
                (batch_id, agent, model, input_tokens, output_tokens,
                 cost_eur, latency_ms, tool_calls)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                batch_id,
                agent,
                model,
                input_tokens,
                output_tokens,
                cost_eur,
                latency_ms,
                Jsonb(tool_calls),
            ),
        )
        conn.execute(
            """
            UPDATE batches
            SET cost_eur = cost_eur + %s, updated_at = now()
            WHERE batch_id = %s
            """,
            (cost_eur, batch_id),
        )
