"""Check the executor's output against the rules. Nothing is changed here.

Each rule is one small function that returns a RuleResult. To add a rule,
write the function and add it to RULES.
"""

from datetime import date

from pydantic import BaseModel

from salesonboard import schema
from salesonboard.pipeline.executor import ExecutionResult, is_empty

# Which Python values count as each schema type.
PYTHON_TYPES = {
    "text": (str,),
    "integer": (int,),
    "numeric": (int, float),
    "date": (date,),
}


class RuleResult(BaseModel):
    rule: str
    check: str
    passed: bool
    failing_rows: list[int] = []  # source_row numbers
    message: str = ""


def values_by_row(result: ExecutionResult, field_name: str):
    """Pairs of (source_row, value) for one field, so a failure names its row."""
    return zip(result.rows["source_row"], result.rows[field_name], strict=True)


def r1_required_not_null(result: ExecutionResult) -> RuleResult:
    failing = set()
    for field_name in schema.required_fields():
        for source_row, value in values_by_row(result, field_name):
            if is_empty(value):
                failing.add(int(source_row))

    return RuleResult(
        rule="R1",
        check="Required fields are not null",
        passed=len(failing) == 0,
        failing_rows=sorted(failing),
    )


def r2_values_have_their_type(result: ExecutionResult) -> RuleResult:
    # Values the executor could not convert.
    failing = set()
    for error in result.parse_errors:
        failing.add(error.source_row)

    # Values the plan never converted, e.g. a date column left as text.
    for field in schema.FIELDS:
        allowed_types = PYTHON_TYPES[field.type]
        for source_row, value in values_by_row(result, field.name):
            if is_empty(value):
                continue  # missing values are R1's job
            if isinstance(value, bool) or not isinstance(value, allowed_types):
                failing.add(int(source_row))

    return RuleResult(
        rule="R2",
        check="Values parse to their type (dates, numbers)",
        passed=len(failing) == 0,
        failing_rows=sorted(failing),
    )


def r3_unit_price_not_negative(result: ExecutionResult) -> RuleResult:
    failing = []
    for source_row, price in values_by_row(result, "unit_price"):
        if isinstance(price, (int, float)) and price < 0:
            failing.append(int(source_row))

    return RuleResult(
        rule="R3",
        check="unit_price >= 0",
        passed=len(failing) == 0,
        failing_rows=failing,
    )


def r4_rows_are_preserved(result: ExecutionResult) -> RuleResult:
    rows_out = len(result.rows)
    rows_dropped = len(result.dropped)
    return RuleResult(
        rule="R4",
        check="Rows out + rows dropped = rows in",
        passed=rows_out + rows_dropped == result.rows_in,
        message=f"{rows_out} out + {rows_dropped} dropped, {result.rows_in} in",
    )


RULES = [
    r1_required_not_null,
    r2_values_have_their_type,
    r3_unit_price_not_negative,
    r4_rows_are_preserved,
]


def validate(result: ExecutionResult) -> list[RuleResult]:
    return [rule(result) for rule in RULES]


def all_passed(results: list[RuleResult]) -> bool:
    for result in results:
        if not result.passed:
            return False
    return True
