"""Read an uploaded CSV file exactly as it is written: every value stays text.

Nothing is converted here. Deciding what "06/01/2025" or "1,80" means is the
plan's job, so the profiler and the executor both start from the raw text.
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

CANDIDATE_DELIMITERS = [",", ";", "\t", "|"]


@dataclass
class SourceFile:
    data: pd.DataFrame  # every value is a string; empty cells are ""
    delimiter: str


def detect_delimiter(header_line: str) -> str:
    """The delimiter is the candidate that appears most often in the header."""
    best = ","
    best_count = 0
    for candidate in CANDIDATE_DELIMITERS:
        count = header_line.count(candidate)
        if count > best_count:
            best = candidate
            best_count = count
    return best


def read_source_file(path: Path) -> SourceFile:
    # utf-8-sig also reads files that start with a byte order mark (common from Excel).
    with open(path, encoding="utf-8-sig") as file:
        header_line = file.readline()
    delimiter = detect_delimiter(header_line)

    # dtype=str and keep_default_na=False read every cell as its exact text:
    # "007" stays "007", and an empty cell stays "" instead of becoming NaN.
    data = pd.read_csv(
        path,
        sep=delimiter,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )
    return SourceFile(data=data, delimiter=delimiter)
