"""Batch statuses and which moves between them are allowed.

A batch's status says which step it is at. It matches the tabs on the web
page, so the page can reopen at the right step after the browser is closed.

    mapping -> questions -> review_mapping -> validating -> summary -> loading -> loaded
                                                ^    |
                                                |    v
                                             fix_problems

- "questions" is only used when the mapping agent marked a column ambiguous.
- "fix_problems" is only used when a rule failed and the investigator has a fix.
- From most steps a batch can go to "support" (a ticket was created) or
  "stopped" (the customer gave up).

Pure logic: no database, no LLM. batch_steps.py calls check_move() before
every status change, so code can never skip a step, for example load data
before the customer approved the summary.
"""

# The status a new batch starts with: the mapping agent runs right after upload.
FIRST_STATUS = "mapping"

# For each status, the statuses it may move to next.
ALLOWED_MOVES: dict[str, list[str]] = {
    # The system is working (background step).
    "mapping": ["questions", "review_mapping", "support"],
    "validating": ["summary", "fix_problems", "support"],
    "loading": ["loaded", "support"],
    # Waiting for the customer.
    "questions": ["review_mapping", "support", "stopped"],
    "review_mapping": ["validating", "support", "stopped"],
    "fix_problems": ["validating", "support", "stopped"],
    "summary": ["loading", "support", "stopped"],
    # Finished: nothing comes after these.
    "loaded": [],
    "support": [],
    "stopped": [],
}

# Must match the one_active_batch_per_tenant index in db/control.sql.
FINISHED = ["loaded", "stopped", "support"]


class InvalidMoveError(Exception):
    """A status change that the workflow does not allow."""


def check_move(current: str, new: str):
    """Raise InvalidMoveError unless a batch may go from current to new."""
    if current not in ALLOWED_MOVES:
        raise ValueError(f"Unknown batch status {current!r}")
    if new not in ALLOWED_MOVES[current]:
        allowed = ", ".join(ALLOWED_MOVES[current]) or "nothing, the batch is finished"
        raise InvalidMoveError(
            f"A batch cannot go from {current!r} to {new!r}. Allowed next: {allowed}."
        )


def is_finished(status: str) -> bool:
    """True when the batch is done, so the customer may upload a new file."""
    return status in FINISHED
