"""Golden test: the correct plan, run on each demo file, must give exactly the
expected rows and pass every rule.

If this fails, the bug is in the executor or the validator, not in an agent.
"""

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from salesonboard.pipeline.executor import execute
from salesonboard.pipeline.source_file import read_source_file
from salesonboard.pipeline.validator import all_passed, validate
from salesonboard.transform_plan import TransformPlan

DEMO_DATA = Path("demo_data")


def load_plan(company: str) -> TransformPlan:
    text = (DEMO_DATA / company / "ground_truth.json").read_text(encoding="utf-8")
    return TransformPlan.model_validate(json.loads(text))


def load_expected_rows(company: str) -> list[dict]:
    """Read expected_rows.csv and convert each value to the type the executor gives."""
    rows = []
    with open(DEMO_DATA / company / "expected_rows.csv", newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            rows.append(
                {
                    "source_row": int(row["source_row"]),
                    "order_id": row["order_id"],
                    "order_date": date.fromisoformat(row["order_date"]),
                    "product_id": row["product_id"],
                    "store_id": row["store_id"],
                    "quantity": float(row["quantity"]),
                    "unit_price": float(row["unit_price"]),
                }
            )
    return rows


@pytest.mark.parametrize("company", ["company1", "company2"])
def test_ground_truth_plan_gives_expected_rows(company):
    source = read_source_file(DEMO_DATA / company / "sales.csv")
    result = execute(source.data, load_plan(company), tenant_id=company, batch_id="golden")

    # Every rule passes.
    rule_results = validate(result)
    assert all_passed(rule_results), [r for r in rule_results if not r.passed]

    # The output equals the expected rows, row by row.
    expected = load_expected_rows(company)
    columns = list(expected[0].keys())
    actual = result.rows[columns].to_dict(orient="records")
    assert len(actual) == len(expected)
    for actual_row, expected_row in zip(actual, expected, strict=True):
        assert actual_row == expected_row

    # System fields are set on every row.
    assert set(result.rows["tenant_id"]) == {company}
    assert set(result.rows["batch_id"]) == {"golden"}


def test_wrong_date_order_is_caught_by_r2():
    # Same plan, but MM/DD instead of DD/MM: "13/01/2025" has no month 13.
    plan = load_plan("company2")
    for operation in plan.operations:
        if operation.op == "parse_date":
            operation.format = "%m/%d/%Y"

    source = read_source_file(DEMO_DATA / "company2" / "sales.csv")
    result = execute(source.data, plan, tenant_id="company2", batch_id="golden")

    rule_results = validate(result)
    r2 = rule_results[1]
    assert r2.rule == "R2"
    assert not r2.passed
    assert len(r2.failing_rows) > 0
