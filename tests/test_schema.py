import pytest

from salesonboard.schema import FIELDS, get_field, mappable_fields, required_fields


def test_fields_match_minimal_schema_in_order():
    assert [field.name for field in FIELDS] == [
        "tenant_id",
        "batch_id",
        "source_row",
        "order_id",
        "order_date",
        "product_id",
        "store_id",
        "quantity",
        "unit_price",
    ]


def test_every_field_is_required_except_store_id():
    assert set(required_fields()) == {f.name for f in FIELDS} - {"store_id"}


def test_system_fields_are_not_mappable():
    names = [field.name for field in mappable_fields()]
    assert names == ["order_id", "order_date", "product_id", "store_id", "quantity", "unit_price"]


def test_get_field_returns_the_field():
    assert get_field("order_date").type == "date"


def test_get_field_rejects_unknown_name():
    with pytest.raises(KeyError, match="nope"):
        get_field("nope")


def test_every_field_has_a_definition():
    assert all(field.definition.strip() for field in FIELDS)


def test_field_names_are_unique():
    names = [field.name for field in FIELDS]
    assert len(names) == len(set(names))
