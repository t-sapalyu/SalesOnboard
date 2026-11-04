"""A fake OpenAI client for agent tests. It plays the model: each call returns
the next prepared reply, so a test decides exactly when the model asks for a
tool and when it answers. No internet, no API key, no cost.
"""

import copy
import json
from types import SimpleNamespace


class ScriptedClient:
    """Stands in for openai.OpenAI. Each call returns the next prepared reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        # The real client is used as client.chat.completions.create(...)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        # Copy, because the loop keeps adding to the same messages list.
        self.requests.append(copy.deepcopy(request))
        message = self.replies.pop(0)
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def answer(text):
    """A reply with a final answer."""
    return SimpleNamespace(role="assistant", content=text, tool_calls=None)


def asks_for_tool(name, arguments, call_id="call_1"):
    """A reply where the model asks for one tool."""
    tool_call = SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments))
    )
    return SimpleNamespace(role="assistant", content=None, tool_calls=[tool_call])


def tool_results(request):
    """The tool messages that were sent in one request."""
    return [message for message in request["messages"] if message["role"] == "tool"]
