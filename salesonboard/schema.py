"""The canonical schema: the one place that says what each target field means.

The mapping agent reads the definitions, the executor reads set_by_system,
the validator reads required and type, and the sales_lines table is built
from the names and types.
"""

from dataclasses import dataclass
from typing import Literal

FieldType = Literal["text", "integer", "numeric", "date"]


@dataclass(frozen=True)
class Field:
    name: str
    type: FieldType
    required: bool
    definition: str
    set_by_system: bool = False  # True: never read from the file


FIELDS: tuple[Field, ...] = (
    Field(
        "tenant_id",
        "text",
        True,
        "The company that uploaded the file.",
        set_by_system=True,
    ),
    Field(
        "batch_id",
        "text",
        True,
        "One per upload.",
        set_by_system=True,
    ),
    Field(
        "source_row",
        "integer",
        True,
        "Data row number in the source file, starting at 1 after the header.",
        set_by_system=True,
    ),
    Field(
        "order_id",
        "text",
        True,
        "Order, transaction or invoice number from the source.",
    ),
    Field(
        "order_date",
        "date",
        True,
        "Date the order was placed or the sale happened at the till. "
        "A ship, delivery or invoice date is never accepted.",
    ),
    Field(
        "product_id",
        "text",
        True,
        "The customer's own product code. Only unique within one tenant.",
    ),
    Field(
        "store_id",
        "text",
        False,
        "The physical store, website or sales point where the sale happened. "
        "Not a warehouse or shelf location.",
    ),
    Field(
        "quantity",
        "numeric",
        True,
        "Units sold.",
    ),
    Field(
        "unit_price",
        "numeric",
        True,
        "Price per unit, excluding tax.",
    ),
)

_BY_NAME = {field.name: field for field in FIELDS}


def get_field(name: str) -> Field:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(f"Unknown canonical field: {name!r}") from None


def required_fields() -> list[str]:
    return [field.name for field in FIELDS if field.required]


def mappable_fields() -> list[Field]:
    """Fields a source column may be mapped to; system fields are excluded."""
    return [field for field in FIELDS if not field.set_by_system]
