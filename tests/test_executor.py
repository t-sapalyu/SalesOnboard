from datetime import date

import pandas as pd
import pytest

from salesonboard.pipeline.executor import execute, text_to_number
from salesonboard.transform_plan import TransformPlan

# A tiny "file": every value is text, like read_source_file gives us.
SOURCE = pd.DataFrame(
    {
        "Order": ["A1", "A2", "A3"],
        "Day": ["06/01/2025", "13/01/2025", "bad date"],
        "SKU": ["SKU-1  ", "", " SKU-3"],
        "Qty": ["2", "1", "4"],
        "Price": ["1.234,50", "12,5", "3"],
    }
)


def mapped(source, target):
    return {"source": source, "status": "mapped", "target": target}


def op(op_id, op_name, **params):
    return {"id": op_id, "op": op_name, "reason": "test", "created_by": "human", **params}


def run(extra_operations=(), extra_columns=()):
    """Map Order, Day and SKU, then run the extra operations."""
    columns = [
        mapped("Order", "order_id"),
        mapped("Day", "order_date"),
        mapped("SKU", "product_id"),
    ]
    operations = [
        op("m1", "map_column", source="Order", target="order_id"),
        op("m2", "map_column", source="Day", target="order_date"),
        op("m3", "map_column", source="SKU", target="product_id"),
    ]
    columns.extend(extra_columns)
    operations.extend(extra_operations)

    plan = TransformPlan.model_validate({"columns": columns, "operations": operations})
    return execute(SOURCE, plan, tenant_id="company1", batch_id="b1")


def values(result, column):
    return list(result.rows[column])


# --- map_column ---------------------------------------------------------------


def test_map_column_copies_values_and_adds_system_fields():
    result = run()
    assert values(result, "order_id") == ["A1", "A2", "A3"]
    assert values(result, "source_row") == [1, 2, 3]
    assert values(result, "tenant_id") == ["company1"] * 3
    assert values(result, "batch_id") == ["b1"] * 3
    assert result.changed_by_operation["m1"] == 3


def test_unmapped_fields_are_empty_columns_in_schema_order():
    result = run()
    assert list(result.rows.columns)[:3] == ["tenant_id", "batch_id", "source_row"]
    assert values(result, "store_id") == [None, None, None]


def test_map_column_fails_when_source_column_is_missing():
    plan = TransformPlan.model_validate(
        {
            "columns": [mapped("Nope", "order_id")],
            "operations": [op("m1", "map_column", source="Nope", target="order_id")],
        }
    )
    with pytest.raises(ValueError, match="not in the file"):
        execute(SOURCE, plan, tenant_id="company1", batch_id="b1")


# --- trim -------------------------------------------------------------------


def test_trim_removes_spaces_and_counts_changes():
    result = run([op("t1", "trim", column="product_id")])
    assert values(result, "product_id") == ["SKU-1", "", "SKU-3"]
    assert result.changed_by_operation["t1"] == 2


# --- drop_rows --------------------------------------------------------------


def test_drop_rows_removes_empty_rows_and_remembers_why():
    result = run([op("d1", "drop_rows", column="product_id", when="empty")])
    assert values(result, "source_row") == [1, 3]
    assert result.rows_in == 3
    assert len(result.dropped) == 1
    assert result.dropped[0].source_row == 2
    assert result.dropped[0].operation_id == "d1"


# --- parse_date -------------------------------------------------------------


def test_parse_date_converts_and_records_bad_values():
    result = run([op("p1", "parse_date", column="order_date", format="%d/%m/%Y")])
    assert values(result, "order_date") == [date(2025, 1, 6), date(2025, 1, 13), None]
    assert result.changed_by_operation["p1"] == 2

    assert len(result.parse_errors) == 1
    error = result.parse_errors[0]
    assert (error.source_row, error.value, error.operation_id) == (3, "bad date", "p1")


# --- cast_number ------------------------------------------------------------


def test_cast_number_with_decimal_comma():
    result = run(
        extra_operations=[
            op("m4", "map_column", source="Price", target="unit_price"),
            op("c1", "cast_number", column="unit_price", decimal_separator=","),
        ],
        extra_columns=[mapped("Price", "unit_price")],
    )
    assert values(result, "unit_price") == [1234.5, 12.5, 3.0]


@pytest.mark.parametrize(
    "text, separator, scale, expected",
    [
        ("12.50", ".", 1.0, 12.5),
        ("1,234.50", ".", 1.0, 1234.5),
        ("1.234,50", ",", 1.0, 1234.5),
        ("1250", ".", 0.01, 12.5),
        ("-3", ".", 1.0, -3.0),
    ],
)
def test_text_to_number(text, separator, scale, expected):
    assert text_to_number(text, separator, scale) == expected


def test_text_to_number_rejects_text():
    with pytest.raises(ValueError):
        text_to_number("abc", ".", 1.0)
