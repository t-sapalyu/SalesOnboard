import pytest
from pydantic import ValidationError

from salesonboard.transform_plan import CastNumber, MapColumn, TransformPlan


def valid_plan_dict():
    """A small, correct plan. Each rejection test breaks exactly one thing in it."""
    return {
        "columns": [
            {"source": "Order", "status": "mapped", "target": "order_id"},
            {"source": "Date", "status": "mapped", "target": "order_date"},
            {"source": "Price", "status": "mapped", "target": "unit_price"},
            {"source": "Customer", "status": "ignored", "reason": "buyer name, not loaded"},
        ],
        "operations": [
            {
                "id": "op1",
                "op": "map_column",
                "source": "Order",
                "target": "order_id",
                "reason": "order number",
                "created_by": "mapping_agent",
            },
            {
                "id": "op2",
                "op": "map_column",
                "source": "Date",
                "target": "order_date",
                "reason": "order date",
                "created_by": "mapping_agent",
            },
            {
                "id": "op3",
                "op": "map_column",
                "source": "Price",
                "target": "unit_price",
                "reason": "price per unit",
                "created_by": "mapping_agent",
            },
            {
                "id": "op4",
                "op": "parse_date",
                "column": "order_date",
                "format": "%d/%m/%Y",
                "reason": "day comes first",
                "created_by": "mapping_agent",
            },
            {
                "id": "op5",
                "op": "cast_number",
                "column": "unit_price",
                "decimal_separator": ",",
                "reason": "decimal comma",
                "created_by": "mapping_agent",
            },
        ],
    }


# --- Valid plans ------------------------------------------------------------


def test_valid_plan_parses_into_the_right_operation_classes():
    plan = TransformPlan.model_validate(valid_plan_dict())
    assert isinstance(plan.operations[0], MapColumn)
    assert isinstance(plan.operations[4], CastNumber)
    assert plan.operations[4].scale == 1.0  # default


def test_plan_survives_a_round_trip_through_json():
    plan = TransformPlan.model_validate(valid_plan_dict())
    assert TransformPlan.model_validate_json(plan.model_dump_json()) == plan


def test_ambiguous_column_with_candidates_and_reason_is_accepted():
    data = valid_plan_dict()
    data["columns"].append(
        {
            "source": "Amount",
            "status": "ambiguous",
            "candidates": ["unit_price", "unmapped: tax-inclusive"],
            "reason": "about 1.2 x price on most rows",
        }
    )
    TransformPlan.model_validate(data)


# --- Rejected operations ----------------------------------------------------


def test_unknown_operation_is_rejected():
    data = valid_plan_dict()
    data["operations"][4]["op"] = "guess_tax"
    with pytest.raises(ValidationError):
        TransformPlan.model_validate(data)


def test_misspelled_key_is_rejected():
    data = valid_plan_dict()
    data["operations"][3]["fromat"] = data["operations"][3].pop("format")
    with pytest.raises(ValidationError):
        TransformPlan.model_validate(data)


def test_bad_decimal_separator_is_rejected():
    data = valid_plan_dict()
    data["operations"][4]["decimal_separator"] = ";"
    with pytest.raises(ValidationError):
        TransformPlan.model_validate(data)


def test_zero_scale_is_rejected():
    data = valid_plan_dict()
    data["operations"][4]["scale"] = 0
    with pytest.raises(ValidationError):
        TransformPlan.model_validate(data)


def test_unknown_creator_is_rejected():
    data = valid_plan_dict()
    data["operations"][0]["created_by"] = "someone"
    with pytest.raises(ValidationError):
        TransformPlan.model_validate(data)


def test_duplicate_operation_ids_are_rejected():
    data = valid_plan_dict()
    data["operations"][4]["id"] = "op1"
    with pytest.raises(ValidationError, match="used more than once"):
        TransformPlan.model_validate(data)


def test_operation_before_its_field_is_mapped_is_rejected():
    data = valid_plan_dict()
    data["operations"].insert(0, data["operations"].pop(3))  # parse_date first
    with pytest.raises(ValidationError, match="before it is mapped"):
        TransformPlan.model_validate(data)


# --- Rejected column decisions ----------------------------------------------


@pytest.mark.parametrize("target", ["tenant_id", "nope"])
def test_target_that_is_not_mappable_is_rejected(target):
    data = valid_plan_dict()
    data["columns"][0]["target"] = target
    data["operations"][0]["target"] = target
    with pytest.raises(ValidationError, match="not a field you can map to"):
        TransformPlan.model_validate(data)


def test_source_column_listed_twice_is_rejected():
    data = valid_plan_dict()
    data["columns"].append({"source": "Order", "status": "ignored", "reason": "duplicate"})
    with pytest.raises(ValidationError, match="appears more than once"):
        TransformPlan.model_validate(data)


def test_two_columns_mapped_to_the_same_target_are_rejected():
    data = valid_plan_dict()
    data["columns"][1]["target"] = "order_id"
    with pytest.raises(ValidationError, match="Two source columns"):
        TransformPlan.model_validate(data)


def test_mapped_column_without_map_operation_is_rejected():
    data = valid_plan_dict()
    data["columns"].append({"source": "Qty", "status": "mapped", "target": "quantity"})
    with pytest.raises(ValidationError, match="do not match"):
        TransformPlan.model_validate(data)


def test_mapped_column_without_target_is_rejected():
    data = valid_plan_dict()
    del data["columns"][0]["target"]
    with pytest.raises(ValidationError, match="has no target"):
        TransformPlan.model_validate(data)


def test_ambiguous_column_with_one_candidate_is_rejected():
    data = valid_plan_dict()
    data["columns"].append(
        {"source": "Amount", "status": "ambiguous", "candidates": ["unit_price"], "reason": "?"}
    )
    with pytest.raises(ValidationError, match="at least 2 candidates"):
        TransformPlan.model_validate(data)


def test_ignored_column_without_reason_is_rejected():
    data = valid_plan_dict()
    del data["columns"][3]["reason"]
    with pytest.raises(ValidationError, match="needs a reason"):
        TransformPlan.model_validate(data)
