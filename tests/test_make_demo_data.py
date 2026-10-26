import csv
import json
from pathlib import Path

import pytest

from salesonboard.transform_plan import TransformPlan
from scripts.make_demo_data import main

COMMITTED_DEMO_DATA = Path("demo_data")
COMPANIES = ["company1", "company2"]
FILES = ["sales.csv", "expected_rows.csv", "ground_truth.json"]
DELIMITERS = {"company1": ",", "company2": ";"}


def read_csv(path: Path, delimiter: str = ",") -> list[dict]:
    with open(path, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file, delimiter=delimiter))


def read_sales(folder: Path, company: str) -> list[dict]:
    return read_csv(folder / company / "sales.csv", DELIMITERS[company])


def read_expected(folder: Path, company: str) -> list[dict]:
    return read_csv(folder / company / "expected_rows.csv")


@pytest.fixture
def generated(tmp_path) -> Path:
    main(tmp_path)
    return tmp_path


# --- Checks for every company -----------------------------------------------


@pytest.mark.parametrize("company", COMPANIES)
def test_two_runs_write_identical_files(company, generated, tmp_path_factory):
    second = tmp_path_factory.mktemp("second")
    main(second)
    for name in FILES:
        first_run = (generated / company / name).read_bytes()
        second_run = (second / company / name).read_bytes()
        assert first_run == second_run, name


@pytest.mark.parametrize("company", COMPANIES)
def test_committed_demo_data_is_up_to_date(company, generated):
    # If this fails, run: uv run python -m scripts.make_demo_data
    for name in FILES:
        committed = (COMMITTED_DEMO_DATA / company / name).read_bytes()
        assert committed == (generated / company / name).read_bytes(), name


@pytest.mark.parametrize("company", COMPANIES)
def test_ground_truth_is_a_valid_plan(company, generated):
    text = (generated / company / "ground_truth.json").read_text(encoding="utf-8")
    TransformPlan.model_validate(json.loads(text))


@pytest.mark.parametrize("company", COMPANIES)
def test_file_has_about_700_lines(company, generated):
    assert 600 <= len(read_sales(generated, company)) <= 800


# --- company1 ---------------------------------------------------------------


def test_company1_drops_rows_without_sku(generated):
    sales = read_sales(generated, "company1")
    expected = read_expected(generated, "company1")

    empty_sku_rows = [row for row in sales if row["SKU"].strip() == ""]
    assert len(empty_sku_rows) == 4
    assert len(expected) == len(sales) - len(empty_sku_rows)


def test_company1_expected_rows_point_back_to_the_right_source_row(generated):
    sales = read_sales(generated, "company1")
    expected = read_expected(generated, "company1")

    for row in expected:
        source = sales[int(row["source_row"]) - 1]  # source_row starts at 1
        assert source["OrderNo"] == row["order_id"]
        assert source["SKU"].strip() == row["product_id"]


# --- company2 ---------------------------------------------------------------


def test_company2_keeps_every_row(generated):
    assert len(read_expected(generated, "company2")) == len(read_sales(generated, "company2"))


def test_company2_uses_french_formats(generated):
    sales = read_sales(generated, "company2")
    expected = read_expected(generated, "company2")

    first_source = sales[0]
    first_expected = expected[0]
    # DD/MM/YYYY in the file, ISO in the expected rows
    day, month, year = first_source["Date"].split("/")
    assert f"{year}-{month}-{day}" == first_expected["order_date"]
    # Decimal comma in the file, decimal point in the expected rows
    assert first_source["Prix unitaire"].replace(",", ".") == first_expected["unit_price"]


def test_company2_has_dates_that_only_work_day_first(generated):
    # A day above 12 can only be read as DD/MM. Without these, the profiler
    # could not tell DD/MM from MM/DD.
    days = [int(row["Date"].split("/")[0]) for row in read_sales(generated, "company2")]
    assert max(days) > 12
