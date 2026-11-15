"""Tests for batch_status.py. No database needed."""

import pytest

from salesonboard.db.control import CONTROL_SQL
from salesonboard.workflow.batch_status import (
    ALLOWED_MOVES,
    FINISHED,
    FIRST_STATUS,
    InvalidMoveError,
    check_move,
    is_finished,
)


def all_allowed_moves():
    """Every (current, new) pair in ALLOWED_MOVES."""
    moves = []
    for current, next_statuses in ALLOWED_MOVES.items():
        for new in next_statuses:
            moves.append((current, new))
    return moves


# --- Allowed moves ----------------------------------------------------------


@pytest.mark.parametrize("current, new", all_allowed_moves())
def test_every_allowed_move_is_accepted(current, new):
    check_move(current, new)  # does not raise


def test_happy_path_without_questions_or_problems():
    path = ["mapping", "review_mapping", "validating", "summary", "loading", "loaded"]
    for current, new in zip(path, path[1:], strict=False):
        check_move(current, new)


def test_path_with_questions_and_a_fix():
    path = [
        "mapping",
        "questions",
        "review_mapping",
        "validating",
        "fix_problems",
        "validating",
        "summary",
        "loading",
        "loaded",
    ]
    for current, new in zip(path, path[1:], strict=False):
        check_move(current, new)


# --- Illegal moves ----------------------------------------------------------


@pytest.mark.parametrize(
    "current, new",
    [
        ("review_mapping", "loading"),  # load before validation and summary
        ("validating", "loading"),  # load before the customer approved the summary
        ("mapping", "validating"),  # skip the customer's mapping review
        ("fix_problems", "summary"),  # skip validating the fixed plan
        ("summary", "mapping"),  # go back to the start
        ("loading", "stopped"),  # stop while the data is being written
        ("loaded", "loading"),  # load the same batch twice
        ("support", "validating"),  # continue after support took over
    ],
)
def test_illegal_move_is_rejected(current, new):
    with pytest.raises(InvalidMoveError, match="cannot go from"):
        check_move(current, new)


def test_unknown_status_is_rejected():
    with pytest.raises(ValueError, match="Unknown batch status"):
        check_move("almost_done", "loaded")


def test_finished_batch_error_says_so():
    with pytest.raises(InvalidMoveError, match="the batch is finished"):
        check_move("loaded", "summary")


# --- The status list itself -------------------------------------------------


def test_every_next_status_is_a_known_status():
    for next_statuses in ALLOWED_MOVES.values():
        for status in next_statuses:
            assert status in ALLOWED_MOVES


def test_first_status_is_known_and_active():
    assert FIRST_STATUS in ALLOWED_MOVES
    assert not is_finished(FIRST_STATUS)


def test_finished_statuses_have_no_next_status():
    for status in FINISHED:
        assert is_finished(status)
        assert ALLOWED_MOVES[status] == []


def test_every_active_status_can_reach_support():
    # A ticket must always be possible while a batch is active.
    for status, next_statuses in ALLOWED_MOVES.items():
        if not is_finished(status):
            assert "support" in next_statuses


def test_finished_statuses_match_the_database_index():
    # The one_active_batch_per_tenant index in control.sql lists the same
    # statuses; if one changes without the other, this test fails.
    sql = CONTROL_SQL.read_text(encoding="utf-8")
    statuses = ", ".join(f"'{status}'" for status in FINISHED)
    assert f"WHERE status NOT IN ({statuses})" in sql
