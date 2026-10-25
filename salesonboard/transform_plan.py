"""The transformation plan: the list of steps that turns one customer's file
into canonical rows.

Agents write a plan, the customer approves it, and the executor runs it.
Agents never touch the data themselves. Because the plan often comes from an
LLM, these models are strict: anything unknown or inconsistent is rejected
here, before the executor ever sees it.

Convention: map_column comes first, and every later operation refers to the
canonical field name (for example "unit_price"), not the source column name.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from salesonboard import schema

CreatedBy = Literal["mapping_agent", "investigator", "human"]
ColumnStatus = Literal["mapped", "ambiguous", "unmapped", "ignored"]


class StrictModel(BaseModel):
    # Reject unknown keys, so a typo like "fromat" fails instead of being ignored.
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Operations. Every operation has an id, a reason, and who created it,
# so each row change can be traced back to one plan entry.
# ---------------------------------------------------------------------------


class BaseOperation(StrictModel):
    id: str
    reason: str
    created_by: CreatedBy


class MapColumn(BaseOperation):
    """Copy a source column into a canonical field."""

    op: Literal["map_column"]
    source: str
    target: str


class Trim(BaseOperation):
    """Remove spaces around text values."""

    op: Literal["trim"]
    column: str


class CastNumber(BaseOperation):
    """Turn text like "1.234,5" into a number, then multiply by scale."""

    op: Literal["cast_number"]
    column: str
    decimal_separator: Literal[".", ","] = "."
    scale: float = Field(default=1.0, gt=0)  # e.g. 0.01 for prices in cents


class ParseDate(BaseOperation):
    """Turn text into a date using a strptime format such as %d/%m/%Y."""

    op: Literal["parse_date"]
    column: str
    format: str


class DropRows(BaseOperation):
    """Drop rows where the column is empty. The dropped count is reported."""

    op: Literal["drop_rows"]
    column: str
    when: Literal["empty"]


Operation = MapColumn | Trim | CastNumber | ParseDate | DropRows


def column_written_by(operation: Operation) -> str:
    """The canonical field an operation works on."""
    if isinstance(operation, MapColumn):
        return operation.target
    return operation.column


# ---------------------------------------------------------------------------
# Column decisions. Every source column ends in exactly one status.
# ---------------------------------------------------------------------------


class ColumnDecision(StrictModel):
    source: str
    status: ColumnStatus
    target: str | None = None  # only for "mapped"
    candidates: list[str] = []  # only for "ambiguous"
    reason: str = ""

    @model_validator(mode="after")
    def check_status_details(self):
        if self.status == "mapped" and self.target is None:
            raise ValueError(f"Column {self.source!r} is mapped but has no target")

        if self.status != "mapped" and self.target is not None:
            raise ValueError(f"Column {self.source!r} has a target but is not mapped")

        if self.status == "ambiguous" and len(self.candidates) < 2:
            raise ValueError(f"Ambiguous column {self.source!r} needs at least 2 candidates")

        if self.status != "mapped" and not self.reason:
            raise ValueError(f"Column {self.source!r} is {self.status} and needs a reason")

        return self


# ---------------------------------------------------------------------------
# The whole plan.
# ---------------------------------------------------------------------------


class TransformPlan(StrictModel):
    columns: list[ColumnDecision]
    operations: list[Operation]

    @model_validator(mode="after")
    def check_plan(self):
        self._check_source_columns_are_unique()
        self._check_targets_are_mappable_and_unique()
        self._check_operation_ids_are_unique()
        self._check_operations_use_mapped_fields()
        self._check_map_operations_match_columns()
        return self

    def _check_source_columns_are_unique(self):
        seen = set()
        for column in self.columns:
            if column.source in seen:
                raise ValueError(f"Source column {column.source!r} appears more than once")
            seen.add(column.source)

    def _check_targets_are_mappable_and_unique(self):
        mappable = {field.name for field in schema.mappable_fields()}
        seen = set()
        for column in self.columns:
            if column.target is None:
                continue
            if column.target not in mappable:
                raise ValueError(f"{column.target!r} is not a field you can map to")
            if column.target in seen:
                raise ValueError(f"Two source columns are mapped to {column.target!r}")
            seen.add(column.target)

    def _check_operation_ids_are_unique(self):
        seen = set()
        for operation in self.operations:
            if operation.id in seen:
                raise ValueError(f"Operation id {operation.id!r} is used more than once")
            seen.add(operation.id)

    def _check_operations_use_mapped_fields(self):
        # Walk the operations in order. A field must be mapped before any
        # other operation can use it.
        mapped_so_far = set()
        for operation in self.operations:
            field = column_written_by(operation)
            if isinstance(operation, MapColumn):
                if field in mapped_so_far:
                    raise ValueError(f"{field!r} is mapped more than once")
                mapped_so_far.add(field)
            elif field not in mapped_so_far:
                raise ValueError(f"Operation {operation.id!r} uses {field!r} before it is mapped")

    def _check_map_operations_match_columns(self):
        # The column list and the map_column operations must tell the same story.
        from_columns = set()
        for column in self.columns:
            if column.status == "mapped":
                from_columns.add((column.source, column.target))

        from_operations = set()
        for operation in self.operations:
            if isinstance(operation, MapColumn):
                from_operations.add((operation.source, operation.target))

        if from_columns != from_operations:
            raise ValueError(
                "Mapped columns and map_column operations do not match: "
                f"columns say {sorted(from_columns)}, operations say {sorted(from_operations)}"
            )
