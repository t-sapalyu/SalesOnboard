"""Run a confirmed TransformPlan on the source data.

No LLM and no guessing: each operation does exactly what the plan says.
Every change is counted per operation, and every dropped row remembers which
operation dropped it, so each stored value traces back to one plan entry.

The executor never stops on a bad value. A value that cannot be converted
becomes empty and is recorded as a parse error; the validator reports it.
"""

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from salesonboard import schema
from salesonboard.transform_plan import (
    CastNumber,
    DropRows,
    MapColumn,
    ParseDate,
    TransformPlan,
    Trim,
)


@dataclass
class ParseError:
    source_row: int
    column: str
    value: str
    operation_id: str


@dataclass
class DroppedRow:
    source_row: int
    operation_id: str


@dataclass
class ExecutionResult:
    rows: pd.DataFrame  # canonical rows, columns in schema order
    rows_in: int
    dropped: list[DroppedRow]
    parse_errors: list[ParseError]
    changed_by_operation: dict[str, int]  # operation id -> rows or values it changed


def is_empty(value) -> bool:
    """None, NaN, or text with nothing but spaces."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, float):
        return pd.isna(value)
    return False


def text_to_number(text: str, decimal_separator: str, scale: float) -> float:
    """ "1.234,5" with separator "," -> 1234.5. The other separator is treated
    as a thousands separator and removed."""
    if decimal_separator == ",":
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    # round() removes float noise such as 1250 * 0.01 = 12.500000000000002
    return round(float(text) * scale, 6)


def text_to_date(text: str, date_format: str) -> date:
    return datetime.strptime(text, date_format).date()


def execute(
    source: pd.DataFrame, plan: TransformPlan, tenant_id: str, batch_id: str
) -> ExecutionResult:
    runner = PlanRunner(source)
    runner.run(plan)
    return runner.result(tenant_id, batch_id)


class PlanRunner:
    """Holds the data while the operations run, one after the other.

    Both tables are indexed by source_row (1, 2, 3, ...), so a value can
    always be traced back to its row in the file, even after rows are dropped.
    """

    def __init__(self, source: pd.DataFrame):
        self.source = source.copy()
        self.source.index = range(1, len(source) + 1)
        self.rows = pd.DataFrame(index=self.source.index)
        self.dropped: list[DroppedRow] = []
        self.parse_errors: list[ParseError] = []
        self.changed_by_operation: dict[str, int] = {}

    def run(self, plan: TransformPlan):
        handlers = {
            "map_column": self.map_column,
            "trim": self.trim,
            "cast_number": self.cast_number,
            "parse_date": self.parse_date,
            "drop_rows": self.drop_rows,
        }
        for operation in plan.operations:
            handler = handlers[operation.op]
            self.changed_by_operation[operation.id] = handler(operation)

    # --- Operations. Each one returns how many rows or values it changed. ---

    def map_column(self, operation: MapColumn) -> int:
        if operation.source not in self.source.columns:
            raise ValueError(f"Column {operation.source!r} is not in the file")
        # Assignment matches rows by source_row, so rows dropped earlier stay dropped.
        self.rows[operation.target] = self.source[operation.source].astype(object)
        return len(self.rows)

    def trim(self, operation: Trim) -> int:
        changed = 0
        for source_row, value in self.rows[operation.column].items():
            if isinstance(value, str) and value != value.strip():
                self.rows.at[source_row, operation.column] = value.strip()
                changed += 1
        return changed

    def cast_number(self, operation: CastNumber) -> int:
        def convert(text):
            return text_to_number(text, operation.decimal_separator, operation.scale)

        return self.convert_values(operation.column, operation.id, convert)

    def parse_date(self, operation: ParseDate) -> int:
        def convert(text):
            return text_to_date(text, operation.format)

        return self.convert_values(operation.column, operation.id, convert)

    def drop_rows(self, operation: DropRows) -> int:
        to_drop = []
        for source_row, value in self.rows[operation.column].items():
            if is_empty(value):
                to_drop.append(source_row)
                self.dropped.append(DroppedRow(source_row, operation.id))
        self.rows = self.rows.drop(index=to_drop)
        return len(to_drop)

    # --- Helpers ---

    def convert_values(self, column: str, operation_id: str, convert) -> int:
        """Apply convert to every text value in the column.

        Empty cells become None. A value that cannot be converted also becomes
        None, and is recorded as a parse error so rule R2 can report it.
        """
        new_values = []
        converted = 0
        for source_row, value in self.rows[column].items():
            if not isinstance(value, str):  # already converted, or missing
                new_values.append(value)
                continue

            text = value.strip()
            if text == "":
                new_values.append(None)
                continue

            try:
                new_values.append(convert(text))
                converted += 1
            except ValueError:
                new_values.append(None)
                self.parse_errors.append(ParseError(source_row, column, value, operation_id))

        self.rows[column] = pd.Series(new_values, index=self.rows.index, dtype=object)
        return converted

    def result(self, tenant_id: str, batch_id: str) -> ExecutionResult:
        rows = self.rows.copy()
        rows["tenant_id"] = tenant_id
        rows["batch_id"] = batch_id
        rows["source_row"] = list(rows.index)

        # A field the plan did not map (for example an optional store_id) is
        # still a column, just empty. A missing required field fails rule R1.
        for field in schema.FIELDS:
            if field.name not in rows.columns:
                rows[field.name] = pd.Series([None] * len(rows), index=rows.index, dtype=object)

        field_names = [field.name for field in schema.FIELDS]
        return ExecutionResult(
            rows=rows[field_names].reset_index(drop=True),
            rows_in=len(self.source),
            dropped=self.dropped,
            parse_errors=self.parse_errors,
            changed_by_operation=self.changed_by_operation,
        )
