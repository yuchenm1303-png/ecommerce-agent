from __future__ import annotations

from types import SimpleNamespace

from app.makro.fast_execution import (
    FAST_BATCH_SIZE,
    _fast_batch_eligible,
    _policy_blank_reason,
)
from app.resolution_types import RESOLVED, ResolvedAnswer


def _answer(*, values: list[str] | None = None, qualifier: str | None = None) -> ResolvedAnswer:
    values = list(values or ["Example"])
    return ResolvedAnswer(
        attribute_key="example",
        label="Example",
        status=RESOLVED,
        answer=values[0] if values else None,
        answer_values=values,
        qualifier=qualifier,
    )


def _field(kind: str = "input", *, multi: bool = False) -> dict[str, object]:
    return {
        "attribute_key": "example",
        "label": "Example",
        "multi_value": multi,
        "has_add_value_control": multi,
        "controls": [
            {
                "id": "example",
                "name": "example_0_value",
                "field_kind": kind,
                "type": "text",
            }
        ],
    }


def test_fast_batch_is_bounded() -> None:
    assert FAST_BATCH_SIZE == 8


def test_plain_single_text_field_is_fast_batch_eligible() -> None:
    assert _fast_batch_eligible(_field("input"), _answer()) is True
    assert _fast_batch_eligible(_field("textarea"), _answer()) is True


def test_dynamic_or_composite_fields_stay_on_safe_lane() -> None:
    assert _fast_batch_eligible(_field("dropdown"), _answer()) is False
    assert _fast_batch_eligible(_field("input", multi=True), _answer(values=["A", "B"])) is False
    assert _fast_batch_eligible(_field("input"), _answer(qualifier="cm")) is False


def test_certifications_and_ingredients_are_explicit_leave_blank_policy() -> None:
    certification = SimpleNamespace(attribute_key="certifications", label="Certifications")
    ingredients = SimpleNamespace(attribute_key="ingredients", label="Ingredients")
    ordinary = SimpleNamespace(attribute_key="material", label="Material")

    assert _policy_blank_reason(certification) == "seller_policy_leave_blank"
    assert _policy_blank_reason(ingredients) == "seller_policy_leave_blank"
    assert _policy_blank_reason(ordinary) is None


def test_policy_matching_tolerates_live_key_formatting() -> None:
    item = SimpleNamespace(attribute_key="product_ingredients", label="Ingredients")
    assert _policy_blank_reason(item) == "seller_policy_leave_blank"


def test_production_executor_is_wired_to_fast_section_policy() -> None:
    import makro_execute_listing

    assert makro_execute_listing._fill_one_section.__module__ == "app.makro.fast_execution"
