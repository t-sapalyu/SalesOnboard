"""Support tickets: a person from the team takes over the batch.

A ticket is created in two ways:
- manually: the customer presses "Send to support" (reason "customer"),
- automatically: the system cannot continue on its own, e.g. the €1 cost
  limit was reached or the investigator used its 2 rounds.

Either way the batch moves to "support" and the system stops working on it.
The customer can then upload a new file. In Phase 1 nothing is sent anywhere;
the team reads the support_tickets table.
"""

from salesonboard.agents.llm import CostLimitError
from salesonboard.agents.tool_loop import InvalidOutputError, ToolLimitError
from salesonboard.db import connect
from salesonboard.db.control import get_batch, insert_support_ticket, update_batch
from salesonboard.workflow.batch_status import InvalidMoveError, check_move

# Why a ticket was created.
REASONS = {
    "customer": "The customer pressed Send to support",
    "cost_limit": "The next LLM call could pass the batch's cost limit",
    "tool_limit": "An agent used too many tool calls",
    "invalid_output": "An agent's answer was not valid, even after one retry",
    "round_limit": "The investigator already used all its rounds",
    "no_fix": "The investigator found no fix for the failed rules",
}

# The customer can press "Send to support" only while the batch waits for
# them, not while an agent or the load is running.
CUSTOMER_CAN_SEND_FROM = ["questions", "review_mapping", "fix_problems", "summary"]

# Agent errors that stop the batch and create a ticket, instead of crashing.
AGENT_ERRORS = (CostLimitError, ToolLimitError, InvalidOutputError)


def send_to_support(batch_id: str, reason: str, note: str | None = None) -> int:
    """Create a ticket and move the batch to "support", in one transaction.

    Returns the ticket number, to show to the customer.
    """
    if reason not in REASONS:
        raise ValueError(f"Unknown support reason {reason!r}")

    with connect() as conn:
        batch = get_batch(conn, batch_id, lock=True)
        if batch is None:
            raise ValueError(f"Unknown batch {batch_id!r}")
        if reason == "customer" and batch["status"] not in CUSTOMER_CAN_SEND_FROM:
            raise InvalidMoveError(
                f"Batch {batch_id} is at {batch['status']!r}; wait until it needs you."
            )
        check_move(batch["status"], "support")
        ticket_id = insert_support_ticket(
            conn,
            batch_id=batch_id,
            tenant_id=batch["tenant_id"],
            step=batch["status"],  # where the batch was when it stopped
            reason=reason,
            note=note,
        )
        update_batch(conn, batch_id, status="support")
    return ticket_id


def reason_for_agent_error(error: Exception) -> str:
    """The ticket reason for one of AGENT_ERRORS."""
    if isinstance(error, CostLimitError):
        return "cost_limit"
    if isinstance(error, ToolLimitError):
        return "tool_limit"
    return "invalid_output"
