from __future__ import annotations

from app.makro.domain import MakroDomainAdapter
from app.makro.field_engine import execution_contract
from app.makro_dryrun import _preflight_answer_capacity
from app.resolution_types import RESOLVED, ResolvedAnswer


def _answer(value: str, qualifier: str | None) -> ResolvedAnswer:
    return ResolvedAnswer(
        attribute_key="battery_size",
        label="Battery Size",
        status=RESOLVED,
        answer=value,
        answer_values=[value],
        qualifier=qualifier,
        source_type="ai_decision",
    )


def _plain_text_field() -> dict[str, object]:
    return {
        "attribute_key": "battery_size",
        "label": "Battery Size",
        "required": False,
        "multi_value": False,
        "controls": [
            {
                "id": "battery_size",
                "name": "battery_size_0_value",
                "field_kind": "input",
                "type": "text",
                "context_text": "Battery Size * | Additional Description",
            }
        ],
    }


def test_plain_text_field_serializes_ai_qualifier_at_execution_boundary():
    field = _plain_text_field()
    answer = _answer("600", "mAh")
    projected = MakroDomainAdapter(object())._constrained_execution_answer(field, answer)

    assert execution_contract(field, projected).live_family == "text"
    assert projected.answer_values == ["600 mAh"]
    assert projected.qualifier is None
    assert _preflight_answer_capacity(field, projected) is None
    assert answer.answer_values == ["600"]
    assert answer.qualifier == "mAh"


def test_plain_text_projection_does_not_duplicate_existing_qualifier():
    field = _plain_text_field()
    projected = MakroDomainAdapter(object())._constrained_execution_answer(
        field,
        _answer("600mAh", "mAh"),
    )

    assert projected.answer_values == ["600mAh"]
    assert projected.qualifier is None


def test_numeric_without_live_unit_contract_remains_fail_closed():
    field = _plain_text_field()
    field["controls"][0]["type"] = "number"
    answer = _answer("600", "mAh")
    adapter = MakroDomainAdapter(object())

    projected = adapter._constrained_execution_answer(field, answer)
    error = _preflight_answer_capacity(field, projected)

    assert projected is answer
    assert execution_contract(field, projected).live_family == "numeric"
    assert error is not None
    assert "qualifier control" in error
