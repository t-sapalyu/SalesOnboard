from datetime import date

import pandas as pd

from salesonboard import schema
from salesonboard.pipeline.executor import DroppedRow, ExecutionResult, ParseError
from salesonboard.pipeline.validator import (
    all_passed,
    r1_required_not_null,
    r2_values_have_their_type,
    r3_unit_price_not_negative,
    r4_rows_are_preserved,
    validate,
)


def good_row(source_row):
    return {
        "tenant_id": "company1",
        "batch_id": "b1",
        "source_row": source_row,
        "order_id": f"A{source_row}",
        "order_date": date(2025, 1, 6),
        "product_id": "SKU-1",
        "store_id": "STORE-01",
        "quantity": 2.0,
        "unit_price": 4.99,
    }


def make_result(rows, rows_in=None, dropped=(), parse_errors=()):
    """An ExecutionResult built by hand, so each test controls exactly one problem."""
    frame = pd.DataFrame(rows, columns=[field.name for field in schema.FIELDS], dtype=object)
    return ExecutionResult(
        rows=frame,
        rows_in=len(rows) if rows_in is None else rows_in,
        dropped=list(dropped),
        parse_errors=list(parse_errors),
        changed_by_operation={},
    )


def test_good_rows_pass_every_rule():
    results = validate(make_result([good_row(1), good_row(2)]))
    assert all_passed(results)
    assert [result.rule for result in results] == ["R1", "R2", "R3", "R4"]


# --- R1 ---------------------------------------------------------------------


def test_r1_fails_on_missing_required_value():
    row = good_row(2)
    row["order_id"] = None
    result = r1_required_not_null(make_result([good_row(1), row]))
    assert not result.passed
    assert result.failing_rows == [2]


def test_r1_ignores_missing_optional_store():
    row = good_row(1)
    row["store_id"] = None
    assert r1_required_not_null(make_result([row])).passed


# --- R2 ---------------------------------------------------------------------


def test_r2_fails_on_parse_errors():
    error = ParseError(source_row=1, column="order_date", value="bad", operation_id="p1")
    result = r2_values_have_their_type(make_result([good_row(1)], parse_errors=[error]))
    assert not result.passed
    assert result.failing_rows == [1]


def test_r2_fails_when_a_date_was_never_converted():
    row = good_row(1)
    row["order_date"] = "06/01/2025"  # still text: the plan forgot parse_date
    result = r2_values_have_their_type(make_result([row]))
    assert not result.passed
    assert result.failing_rows == [1]


def test_r2_fails_when_a_number_is_still_text():
    row = good_row(1)
    row["quantity"] = "2"
    assert not r2_values_have_their_type(make_result([row])).passed


# --- R3 ---------------------------------------------------------------------


def test_r3_fails_on_negative_price():
    row = good_row(2)
    row["unit_price"] = -1.0
    result = r3_unit_price_not_negative(make_result([good_row(1), row]))
    assert not result.passed
    assert result.failing_rows == [2]


def test_r3_accepts_zero_price():
    row = good_row(1)
    row["unit_price"] = 0.0
    assert r3_unit_price_not_negative(make_result([row])).passed


# --- R4 ---------------------------------------------------------------------


def test_r4_passes_when_dropped_rows_are_counted():
    dropped = [DroppedRow(source_row=2, operation_id="d1")]
    result = r4_rows_are_preserved(make_result([good_row(1)], rows_in=2, dropped=dropped))
    assert result.passed
    assert result.message == "1 out + 1 dropped, 2 in"


def test_r4_fails_when_a_row_disappears_without_a_reason():
    result = r4_rows_are_preserved(make_result([good_row(1)], rows_in=2))
    assert not result.passed
