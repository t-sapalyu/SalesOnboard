"""The agent loop, written by hand so every step is visible.

An agent = a system prompt + some tools + the shape of its final answer.
run_agent() repeats:

1. Ask the model (through call_llm, so cost is checked and logged).
2. If the model asks for tools: run each one, send the results back, go to 1.
3. Otherwise the message is the final answer: check it against the Pydantic
   model. If it is invalid, tell the model what was wrong and ask once more.

Two hard stops, so the loop always ends:
- more than MAX_TOOL_CALLS tool calls  -> ToolLimitError
- an invalid answer after the retry     -> InvalidOutputError
The caller (the batch workflow, A5) sends the batch to support for both.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from salesonboard.agents.llm import call_llm

# Trace of every agent run: model calls, tools used, final answer.
# Shown when logging is turned on at INFO level, for example:
#   uv run pytest tests/test_mapping_agent.py -o log_cli=true --log-cli-level=INFO
logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 10
MAX_OUTPUT_RETRIES = 1
LOG_PREVIEW_CHARACTERS = 200  # tool results can be long; log only the start


class ToolLimitError(Exception):
    """The agent asked for more tool calls than allowed."""


class InvalidOutputError(Exception):
    """The final answer did not match the expected shape, even after a retry."""


@dataclass
class Tool:
    """A plain Python function the model may call.

    parameters is a JSON schema for the function's arguments, for example:
        {"type": "object",
         "properties": {"column": {"type": "string"}},
         "required": ["column"]}
    The function must return something json.dumps can handle (dict, list, str ...).
    """

    name: str
    description: str
    parameters: dict
    function: Callable

    def definition(self) -> dict:
        """The tool in the format the OpenAI API expects."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class AgentResult:
    output: BaseModel  # an instance of the output_model given to run_agent
    tool_calls: int  # how many tools the agent used


def run_agent(
    batch_id: str,
    agent: str,
    system_prompt: str,
    user_message: str,
    tools: list[Tool],
    output_model: type[BaseModel],
    client=None,
) -> AgentResult:
    """Run one agent until it gives a valid final answer.

    client is only passed by tests, to use a fake instead of the real API.
    """
    tools_by_name = {tool.name: tool for tool in tools}
    tool_definitions = [tool.definition() for tool in tools]

    messages = [
        {"role": "system", "content": system_prompt + answer_instructions(output_model)},
        {"role": "user", "content": user_message},
    ]
    tool_calls_used = 0
    retries_left = MAX_OUTPUT_RETRIES
    call_number = 0

    logger.info("[%s] start, batch %s, tools: %s", agent, batch_id, list(tools_by_name))

    # Not endless: every turn either uses tool calls (at most MAX_TOOL_CALLS)
    # or uses up a retry (at most MAX_OUTPUT_RETRIES), or returns.
    while True:
        call_number += 1
        response = call_llm(batch_id, agent, messages, tool_definitions, client=client)
        message = response.message
        logger.info(
            "[%s] call %d: %d tokens in, %d out, €%.5f, %d ms",
            agent,
            call_number,
            response.input_tokens,
            response.output_tokens,
            response.cost_eur,
            response.latency_ms,
        )

        if message.tool_calls:
            tool_calls_used += len(message.tool_calls)
            if tool_calls_used > MAX_TOOL_CALLS:
                logger.error("[%s] stopped: %d tool calls", agent, tool_calls_used)
                raise ToolLimitError(
                    f"{agent} asked for {tool_calls_used} tool calls; "
                    f"the limit is {MAX_TOOL_CALLS}."
                )
            messages.append(assistant_message_with_tool_calls(message))
            for tool_call in message.tool_calls:
                result = run_tool(tools_by_name, tool_call)
                logger.info(
                    "[%s]   tool %s(%s) -> %s",
                    agent,
                    tool_call.function.name,
                    tool_call.function.arguments,
                    result[:LOG_PREVIEW_CHARACTERS],
                )
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
            continue

        # No tool calls: this is the final answer.
        try:
            output = output_model.model_validate_json(strip_code_fence(message.content or ""))
            logger.info(
                "[%s] done: valid answer after %d calls and %d tool calls",
                agent,
                call_number,
                tool_calls_used,
            )
            return AgentResult(output=output, tool_calls=tool_calls_used)
        except ValidationError as error:
            if retries_left == 0:
                logger.error("[%s] stopped: invalid answer twice", agent)
                raise InvalidOutputError(
                    f"{agent} gave an invalid answer twice. Last error: {error}"
                ) from error
            logger.warning("[%s] invalid answer, asking once more: %s", agent, error)
            retries_left -= 1
            messages.append({"role": "assistant", "content": message.content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your answer was not valid:\n{error}\n"
                        "Reply again with only the corrected JSON."
                    ),
                }
            )


def answer_instructions(output_model: type[BaseModel]) -> str:
    """Added to the system prompt, so the model knows the exact answer shape."""
    schema = json.dumps(output_model.model_json_schema(), indent=2)
    return (
        "\n\nWhen you are done, reply with only a JSON object (no other text) "
        f"that matches this JSON schema:\n{schema}"
    )


def assistant_message_with_tool_calls(message) -> dict:
    """The model's tool request, as a plain dict, to send back in the next call.
    The API needs it there to match each tool result to its request."""
    tool_calls = []
    for tool_call in message.tool_calls:
        tool_calls.append(
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
        )
    return {"role": "assistant", "content": message.content, "tool_calls": tool_calls}


def run_tool(tools_by_name: dict[str, Tool], tool_call) -> str:
    """Run one tool and return its result as text for the model.

    Problems (unknown tool, bad arguments, an error inside the tool) are
    returned as an error message instead of stopping the agent, so the model
    can read what went wrong and try again. Each try still counts toward the
    tool call limit.
    """
    name = tool_call.function.name
    tool = tools_by_name.get(name)
    if tool is None:
        return f"Error: there is no tool called {name!r}."

    try:
        arguments = json.loads(tool_call.function.arguments or "{}")
        result = tool.function(**arguments)
    except Exception as error:
        return f"Error in {name}: {error}"
    return json.dumps(result, default=str)


def strip_code_fence(text: str) -> str:
    """Models often wrap JSON in a ```json ... ``` block. Remove it if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop the opening ``` or ```json line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text
