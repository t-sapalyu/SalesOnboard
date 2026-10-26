"""Create demo customer files with known correct answers.

For each demo customer this writes, under demo_data/<company>/:
  sales.csv          the customer's export, messy on purpose
  expected_rows.csv  the canonical rows the pipeline should produce
  ground_truth.json  the correct TransformPlan for sales.csv

How it works: first make clean canonical sale lines, then write them out the
way this customer's system would export them. Because we start from the clean
lines, we always know the right answer.

Run from the project root:
  uv run python -m scripts.make_demo_data
"""

import csv
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from salesonboard.transform_plan import TransformPlan

SEED = 42  # fixed, so every run writes exactly the same files
START_DATE = date(2025, 1, 6)
DAYS = 12 * 7
NUMBER_OF_ORDERS = 350  # 1 to 3 lines each, about 700 lines
OUTPUT_DIR = Path("demo_data")

PRODUCTS = {
    "SKU-101": 4.99,
    "SKU-102": 7.50,
    "SKU-103": 12.00,
    "SKU-104": 2.35,
    "SKU-105": 19.90,
    "SKU-106": 3.10,
    "SKU-107": 9.99,
    "SKU-108": 5.25,
    "SKU-109": 24.50,
    "SKU-110": 1.80,
    "SKU-111": 15.00,
    "SKU-112": 6.40,
}

EXPECTED_HEADER = [
    "source_row",
    "order_id",
    "order_date",
    "product_id",
    "store_id",
    "quantity",
    "unit_price",
]


@dataclass
class SaleLine:
    """One clean canonical sale line, before any customer formatting."""

    order_id: str
    order_date: date
    product_id: str
    store_id: str
    quantity: int
    unit_price: float


def make_clean_lines(rng: random.Random, order_prefix: str, stores: list[str]) -> list[SaleLine]:
    # Pick a random date for each order, then sort so order numbers follow time.
    order_dates = []
    for _ in range(NUMBER_OF_ORDERS):
        order_dates.append(START_DATE + timedelta(days=rng.randrange(DAYS)))
    order_dates.sort()

    lines = []
    for number, order_date in enumerate(order_dates, start=1):
        order_id = f"{order_prefix}-{number:05d}"
        store_id = rng.choice(stores)
        products = rng.sample(list(PRODUCTS), k=rng.randint(1, 3))
        for product_id in products:
            line = SaleLine(
                order_id=order_id,
                order_date=order_date,
                product_id=product_id,
                store_id=store_id,
                quantity=rng.randint(1, 10),
                unit_price=PRODUCTS[product_id],
            )
            lines.append(line)
    return lines


def expected_row(source_row: int, line: SaleLine) -> list[str]:
    return [
        str(source_row),
        line.order_id,
        line.order_date.isoformat(),
        line.product_id,
        line.store_id,
        str(line.quantity),
        f"{line.unit_price:.2f}",
    ]


def operation(op_id: str, op: str, reason: str, **params) -> dict:
    """One plan operation, written by hand, so created_by is always the human."""
    return {"id": op_id, "op": op, "reason": reason, "created_by": "human", **params}


# ---------------------------------------------------------------------------
# company1: clean ERP export
# ---------------------------------------------------------------------------

COMPANY1_HEADER = ["OrderNo", "OrderDate", "SKU", "Store", "Qty", "UnitPrice", "SalesRep"]
COMPANY1_STORES = ["STORE-01", "STORE-02", "STORE-03"]
SALES_REPS = ["Alice", "Bram", "Chloe", "Dev"]


def make_company1(rng: random.Random, lines: list[SaleLine]):
    """ISO dates, some SKUs with trailing spaces, a few rows with no SKU
    (the plan drops them), and a SalesRep column that is not loaded."""
    # Each entry: (values as written in the file, clean line or None if dropped)
    file_rows = []
    for line in lines:
        sku = line.product_id
        if rng.random() < 0.2:
            sku = sku + "  "
        values = [
            line.order_id,
            line.order_date.isoformat(),
            sku,
            line.store_id,
            str(line.quantity),
            f"{line.unit_price:.2f}",
            rng.choice(SALES_REPS),
        ]
        file_rows.append((values, line))

    # Add a few broken rows with an empty SKU at random places.
    for _ in range(4):
        copy_of = rng.choice(lines)
        values = [
            copy_of.order_id,
            copy_of.order_date.isoformat(),
            "",
            copy_of.store_id,
            str(copy_of.quantity),
            f"{copy_of.unit_price:.2f}",
            rng.choice(SALES_REPS),
        ]
        file_rows.insert(rng.randrange(len(file_rows)), (values, None))

    plan = {
        "columns": [
            {"source": "OrderNo", "status": "mapped", "target": "order_id"},
            {"source": "OrderDate", "status": "mapped", "target": "order_date"},
            {"source": "SKU", "status": "mapped", "target": "product_id"},
            {"source": "Store", "status": "mapped", "target": "store_id"},
            {"source": "Qty", "status": "mapped", "target": "quantity"},
            {"source": "UnitPrice", "status": "mapped", "target": "unit_price"},
            {"source": "SalesRep", "status": "ignored", "reason": "sales person name, no target"},
        ],
        "operations": [
            operation("op1", "map_column", "order number", source="OrderNo", target="order_id"),
            operation("op2", "map_column", "order date", source="OrderDate", target="order_date"),
            operation("op3", "map_column", "product code", source="SKU", target="product_id"),
            operation("op4", "map_column", "store", source="Store", target="store_id"),
            operation("op5", "map_column", "units sold", source="Qty", target="quantity"),
            operation(
                "op6", "map_column", "price per unit", source="UnitPrice", target="unit_price"
            ),
            operation("op7", "trim", "some SKUs have trailing spaces", column="product_id"),
            operation(
                "op8", "drop_rows", "rows with no product code", column="product_id", when="empty"
            ),
            operation("op9", "parse_date", "ISO dates", column="order_date", format="%Y-%m-%d"),
            operation("op10", "cast_number", "whole numbers", column="quantity"),
            operation("op11", "cast_number", "decimal point", column="unit_price"),
        ],
    }
    return COMPANY1_HEADER, file_rows, plan


# ---------------------------------------------------------------------------
# company2: French shop export
# ---------------------------------------------------------------------------

COMPANY2_HEADER = [
    "N° commande",
    "Date",
    "Réf produit",
    "Magasin",
    "Qté",
    "Prix unitaire",
    "Client",
]
COMPANY2_STORES = ["Paris-01", "Lyon-02", "Lille-03"]
CLIENT_NAMES = ["Dupont", "Martin", "Bernard", "Petit", "Durand", "Moreau"]


def french_number(value: float) -> str:
    """12.5 -> "12,50": France writes a comma as the decimal separator."""
    return f"{value:.2f}".replace(".", ",")


def make_company2(rng: random.Random, lines: list[SaleLine]):
    """Semicolon separated, French headers, DD/MM/YYYY dates, decimal commas,
    and a Client column (a buyer name) that is not loaded."""
    file_rows = []
    for line in lines:
        values = [
            line.order_id,
            line.order_date.strftime("%d/%m/%Y"),
            line.product_id,
            line.store_id,
            str(line.quantity),
            french_number(line.unit_price),
            rng.choice(CLIENT_NAMES),
        ]
        file_rows.append((values, line))

    plan = {
        "columns": [
            {"source": "N° commande", "status": "mapped", "target": "order_id"},
            {"source": "Date", "status": "mapped", "target": "order_date"},
            {"source": "Réf produit", "status": "mapped", "target": "product_id"},
            {"source": "Magasin", "status": "mapped", "target": "store_id"},
            {"source": "Qté", "status": "mapped", "target": "quantity"},
            {"source": "Prix unitaire", "status": "mapped", "target": "unit_price"},
            {"source": "Client", "status": "ignored", "reason": "buyer name, personal data"},
        ],
        "operations": [
            operation("op1", "map_column", "order number", source="N° commande", target="order_id"),
            operation("op2", "map_column", "order date", source="Date", target="order_date"),
            operation(
                "op3", "map_column", "product code", source="Réf produit", target="product_id"
            ),
            operation("op4", "map_column", "store", source="Magasin", target="store_id"),
            operation("op5", "map_column", "units sold", source="Qté", target="quantity"),
            operation(
                "op6", "map_column", "price per unit", source="Prix unitaire", target="unit_price"
            ),
            operation(
                "op7", "parse_date", "day comes first", column="order_date", format="%d/%m/%Y"
            ),
            operation("op8", "cast_number", "whole numbers", column="quantity"),
            operation(
                "op9", "cast_number", "decimal comma", column="unit_price", decimal_separator=","
            ),
        ],
    }
    return COMPANY2_HEADER, file_rows, plan


# ---------------------------------------------------------------------------
# Writing the files
# ---------------------------------------------------------------------------


def write_csv(path: Path, header: list[str], rows: list[list[str]], delimiter: str = ","):
    # newline="" and lineterminator="\n" give the same bytes on Windows and Linux.
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=delimiter, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_company(folder: Path, header, file_rows, plan, delimiter: str = ","):
    TransformPlan.model_validate(plan)  # fail here if the hand-written plan is wrong

    sales_rows = []
    expected_rows = []
    for source_row, (values, line) in enumerate(file_rows, start=1):
        sales_rows.append(values)
        if line is not None:
            expected_rows.append(expected_row(source_row, line))

    folder.mkdir(parents=True, exist_ok=True)
    write_csv(folder / "sales.csv", header, sales_rows, delimiter)
    write_csv(folder / "expected_rows.csv", EXPECTED_HEADER, expected_rows)
    plan_text = json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
    (folder / "ground_truth.json").write_text(plan_text, encoding="utf-8")


def main(output_dir: Path = OUTPUT_DIR):
    rng = random.Random(SEED)

    lines = make_clean_lines(rng, order_prefix="ORD", stores=COMPANY1_STORES)
    header, file_rows, plan = make_company1(rng, lines)
    write_company(output_dir / "company1", header, file_rows, plan)

    # company2 gets its own sale lines, so the two customers have different data.
    lines = make_clean_lines(rng, order_prefix="CMD", stores=COMPANY2_STORES)
    header, file_rows, plan = make_company2(rng, lines)
    write_company(output_dir / "company2", header, file_rows, plan, delimiter=";")


if __name__ == "__main__":
    main()
    print(f"Demo data written to {OUTPUT_DIR}/")
