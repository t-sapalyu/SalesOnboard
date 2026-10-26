from pathlib import Path

from salesonboard.pipeline.profiler import profile_column, profile_file
from salesonboard.pipeline.source_file import detect_delimiter, read_source_file

DEMO_DATA = Path("demo_data")


def column(profile, name):
    for column_profile in profile.columns:
        if column_profile.name == name:
            return column_profile
    raise KeyError(name)


# --- Reading the file -------------------------------------------------------


def test_detect_delimiter():
    assert detect_delimiter("a,b,c\n") == ","
    assert detect_delimiter("N° commande;Date;Qté\n") == ";"


def test_values_are_read_as_exact_text(tmp_path):
    path = tmp_path / "file.csv"
    path.write_text("code,qty\n007,\nSKU-1  ,3\n", encoding="utf-8")
    data = read_source_file(path).data
    assert list(data["code"]) == ["007", "SKU-1  "]
    assert list(data["qty"]) == ["", "3"]


# --- Profiling the demo files -----------------------------------------------


def test_company1_profile():
    profile = profile_file(read_source_file(DEMO_DATA / "company1" / "sales.csv"))

    assert profile.delimiter == ","
    assert profile.row_count == 721

    order_date = column(profile, "OrderDate")
    assert order_date.inferred_type == "date"
    assert order_date.date_order == "YMD"
    assert order_date.min == "2025-01-06"

    sku = column(profile, "SKU")
    assert sku.inferred_type == "text"
    assert sku.values_with_extra_spaces > 0
    assert sku.null_rate > 0  # the 4 rows with no SKU

    assert column(profile, "Qty").inferred_type == "integer"
    assert column(profile, "UnitPrice").decimal_separator == "."


def test_company2_profile_detects_french_formats():
    profile = profile_file(read_source_file(DEMO_DATA / "company2" / "sales.csv"))

    assert profile.delimiter == ";"

    date = column(profile, "Date")
    assert date.inferred_type == "date"
    assert date.date_order == "DMY"

    price = column(profile, "Prix unitaire")
    assert price.inferred_type == "numeric"
    assert price.decimal_separator == ","


# --- Single columns ---------------------------------------------------------


def test_month_first_dates():
    assert profile_column("d", ["01/13/2025", "02/01/2025"]).date_order == "MDY"


def test_date_order_unknown_when_no_part_is_above_12():
    profile = profile_column("d", ["01/02/2025", "03/04/2025"])
    assert profile.inferred_type == "date"
    assert profile.date_order == "unknown"
    assert profile.min is None


def test_numbers_keep_their_written_form_in_min_and_max():
    profile = profile_column("price", ["12,50", "3", "1,80"])
    assert profile.inferred_type == "numeric"
    assert profile.min == "1,80"
    assert profile.max == "12,50"


def test_empty_column():
    profile = profile_column("note", ["", "  ", ""])
    assert profile.inferred_type == "empty"
    assert profile.null_rate == 1.0


def test_mixed_values_are_text():
    assert profile_column("x", ["12", "abc"]).inferred_type == "text"


def test_top_values_are_counted():
    profile = profile_column("store", ["A", "B", "A", "A"])
    assert profile.top_values[0].value == "A"
    assert profile.top_values[0].count == 3
    assert profile.distinct_count == 2
