"""Describe each column of an uploaded file, so the mapping agent can decide
what it means without ever seeing the whole file.

The profile is only evidence. It says "this column looks like dates with the
day first"; it never changes the data.
"""

import re
from collections import Counter
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from salesonboard.pipeline.source_file import SourceFile

TOP_VALUES = 5

INTEGER = re.compile(r"^-?\d+$")
DECIMAL_POINT = re.compile(r"^-?\d+\.\d+$")  # 12.50
DECIMAL_COMMA = re.compile(r"^-?\d+,\d+$")  # 12,50
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # 2025-01-06
SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")  # 06/01/2025

DATE_FORMATS = {"YMD": "%Y-%m-%d", "DMY": "%d/%m/%Y", "MDY": "%m/%d/%Y"}

InferredType = Literal["empty", "integer", "numeric", "date", "text"]
DateOrder = Literal["YMD", "DMY", "MDY", "unknown"]


class TopValue(BaseModel):
    value: str
    count: int


class ColumnProfile(BaseModel):
    name: str
    inferred_type: InferredType
    null_rate: float  # share of empty cells, 0.0 to 1.0
    distinct_count: int
    top_values: list[TopValue]
    min: str | None = None  # numbers as written in the file, dates as YYYY-MM-DD
    max: str | None = None
    decimal_separator: Literal[".", ","] | None = None  # numbers only
    date_order: DateOrder | None = None  # dates only
    values_with_extra_spaces: int = 0  # hint for a trim operation


class FileProfile(BaseModel):
    delimiter: str
    row_count: int
    columns: list[ColumnProfile]


def profile_file(source: SourceFile) -> FileProfile:
    columns = []
    for name in source.data.columns:
        columns.append(profile_column(name, list(source.data[name])))
    return FileProfile(delimiter=source.delimiter, row_count=len(source.data), columns=columns)


def profile_column(name: str, raw_values: list[str]) -> ColumnProfile:
    values = [value.strip() for value in raw_values]
    filled = [value for value in values if value != ""]
    counts = Counter(filled)

    extra_spaces = 0
    for raw, stripped in zip(raw_values, values, strict=True):
        if stripped != "" and raw != stripped:
            extra_spaces += 1

    top_values = []
    for value, count in counts.most_common(TOP_VALUES):
        top_values.append(TopValue(value=value, count=count))

    inferred_type, decimal_separator, date_order = infer_type(filled)
    lowest, highest = value_range(filled, inferred_type, date_order)

    null_rate = 0.0
    if values:
        null_rate = round(1 - len(filled) / len(values), 4)

    return ColumnProfile(
        name=name,
        inferred_type=inferred_type,
        null_rate=null_rate,
        distinct_count=len(counts),
        top_values=top_values,
        min=lowest,
        max=highest,
        decimal_separator=decimal_separator,
        date_order=date_order,
        values_with_extra_spaces=extra_spaces,
    )


def infer_type(filled: list[str]):
    """Return (inferred_type, decimal_separator, date_order) for non-empty values."""
    if not filled:
        return "empty", None, None

    if all_match(filled, [INTEGER]):
        return "integer", None, None
    if all_match(filled, [INTEGER, DECIMAL_POINT]):
        return "numeric", ".", None
    if all_match(filled, [INTEGER, DECIMAL_COMMA]):
        return "numeric", ",", None

    date_order = detect_date_order(filled)
    if date_order is not None:
        return "date", None, date_order

    return "text", None, None


def all_match(values: list[str], patterns: list[re.Pattern]) -> bool:
    """True when every value matches at least one of the patterns."""
    for value in values:
        if not any(pattern.match(value) for pattern in patterns):
            return False
    return True


def detect_date_order(values: list[str]) -> DateOrder | None:
    """Which part of a date comes first. None when the values are not dates.

    For 06/01/2025 we cannot know if the day or the month comes first. Only a
    number above 12 settles it: 13/01/2025 must be day first.
    """
    if all_match(values, [ISO_DATE]):
        return "YMD"

    first_parts = []
    second_parts = []
    for value in values:
        match = SLASH_DATE.match(value)
        if match is None:
            return None
        first_parts.append(int(match.group(1)))
        second_parts.append(int(match.group(2)))

    if max(first_parts) > 12:
        return "DMY"
    if max(second_parts) > 12:
        return "MDY"
    return "unknown"


def value_range(filled: list[str], inferred_type: str, date_order: str | None):
    """Smallest and largest value. None for text, and for dates in unknown order."""
    if inferred_type in ("integer", "numeric"):
        return min(filled, key=text_to_float), max(filled, key=text_to_float)

    if inferred_type == "date" and date_order in DATE_FORMATS:
        date_format = DATE_FORMATS[date_order]
        try:
            dates = [datetime.strptime(value, date_format) for value in filled]
        except ValueError:  # e.g. 31/02/2025
            return None, None
        return min(dates).date().isoformat(), max(dates).date().isoformat()

    return None, None


def text_to_float(text: str) -> float:
    return float(text.replace(",", "."))
