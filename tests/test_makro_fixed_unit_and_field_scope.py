from __future__ import annotations

import inspect

from app.makro.execution import fill_one_section
from app.makro.field_engine import execution_contract
from app.makro.unit_contract import fixed_rendered_unit, validate_answer_unit
from app.makro_dryrun import _preflight_answer_capacity
from app.resolution_types import RESOLVED, ResolvedAnswer


def _answer(key: str, label: str, value: str, qualifier: str | None) -> ResolvedAnswer:
    return ResolvedAnswer(
        attribute_key=key,
        label=label,
        status=RESOLVED,
        answer=value,
        answer_values=[value],
        qualifier=qualifier,
        source_type="ai_decision",
    )


def _fixed_numeric_field(key: str, label: str, unit: str) -> dict[str, object]:
    return {
        "attribute_key": key,
        "label": label,
        "required": True,
        "multi_value": False,
        "controls": [
            {
                "id": key,
                "name": f"{key}_0_value",
                "field_kind": "input",
                "type": "number",
                "context_text": f"{label} *{unit} | Length",
            }
        ],
    }


def test_fixed_cm_breadth_is_plain_numeric_and_passes_unit_preflight():
    field = _fixed_numeric_field("breadth", "Breadth", "CM")
    answer = _answer("breadth", "Breadth", "4.1", "cm")

    assert fixed_rendered_unit(field) == "CM"
    assert validate_answer_unit(field, answer.qualifier) is None
    assert _preflight_answer_capacity(field, answer) is None
    assert execution_contract(field, answer).live_family == "numeric"


def test_fixed_kg_field_rejects_cm_answer_before_any_write():
    field = _fixed_numeric_field("weight", "Weight", "KG")
    answer = _answer("weight", "Weight", "27.2", "cm")

    error = _preflight_answer_capacity(field, answer)

    assert error is not None
    assert "固定单位冲突" in error
    assert "fixed_unit='KG'" in error
    assert execution_contract(field, answer).live_family == "numeric"


def test_real_qualifier_control_remains_numeric_qualified():
    field = {
        "attribute_key": "depth",
        "label": "Depth",
        "required": True,
        "multi_value": False,
        "controls": [
            {
                "id": "depth",
                "name": "depth_0_value",
                "field_kind": "input",
                "type": "number",
                "context_text": "Depth * | Depth",
            },
            {
                "name": "depth_0_qualifier",
                "field_kind": "select",
                "type": "select",
                "options": [
                    {"text": "cm", "value": "cm", "disabled": False},
                    {"text": "mm", "value": "mm", "disabled": False},
                ],
            },
        ],
    }
    answer = _answer("depth", "Depth", "1", "cm")

    assert fixed_rendered_unit(field) == ""
    assert _preflight_answer_capacity(field, answer) is None
    assert execution_contract(field, answer).live_family == "numeric_qualified"


def test_section_executor_no_longer_rolls_back_whole_card_on_one_field_failure():
    source = inspect.getsource(fill_one_section)

    assert "transaction_failed" not in source
    assert "execution_incomplete" not in source
    assert 'report["status"] = "execution_failed_unsaved"' not in source
    assert 'report["unsaved_values_preserved"] = True' in source
    assert 'report["status"] = "persisted_partial"' in source
    assert "仅该字段失败，继续处理同 section 的其他字段" in source
