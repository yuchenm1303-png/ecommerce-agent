from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.ai_decisions import field_id
from app.fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem
from app.required_overrides import (
    FALLBACK_NUMERIC_VALUE,
    FALLBACK_SOURCE_REFERENCE,
    FALLBACK_TEXT_VALUE,
    RequiredOverrideError,
    apply_required_overrides,
    required_fallback_override,
)
from app.resolution_types import MISSING, RESOLVED, ResolutionRecord


ROOT = Path(__file__).resolve().parents[1]
GUI_SOURCE = (ROOT / "gui" / "required_input_support.py").read_text(encoding="utf-8")


def _field(**updates):
    field = {
        "attribute_key": "required_note",
        "label": "Required Note",
        "section_heading": "Product Description (0/10)",
        "required": True,
        "multi_value": False,
        "options": [],
        "qualifier_options": [],
        "help_text": "",
        "context_text": "",
        "controls": [],
    }
    field.update(updates)
    return field


def _blocked_item(field):
    record = ResolutionRecord(
        attribute_key=str(field["attribute_key"]),
        label=str(field["label"]),
        status=MISSING,
        answer=None,
        answer_values=[],
        qualifier=None,
        confidence=0.0,
        source_type=None,
        source_reference=None,
        evidence=None,
        detail="AI left this required field unresolved",
        eligible_for_autofill=False,
        gate_reason="ai_missing",
        question_category=str(field["section_heading"]),
        question_options=list(field.get("options") or []),
    )
    return LiveFillPlanItem(
        attribute_key=str(field["attribute_key"]),
        label=str(field["label"]),
        section_heading=str(field["section_heading"]),
        required=True,
        action=BLOCKED,
        reason=record.detail,
        resolution=record,
    )


def test_free_text_required_gap_uses_na_after_ai_misses():
    fallback = required_fallback_override(_field())

    assert fallback["values"] == [FALLBACK_TEXT_VALUE]
    assert FALLBACK_TEXT_VALUE == "N/A"
    assert fallback["source_type"] == "fallback"


def test_numeric_or_unit_required_gap_uses_one():
    fallback = required_fallback_override(
        _field(
            attribute_key="package_weight",
            label="Package Weight",
            qualifier_options=["kg", "g"],
        )
    )

    assert fallback["values"] == [FALLBACK_NUMERIC_VALUE]
    assert FALLBACK_NUMERIC_VALUE == "1"
    assert fallback["qualifier"] == "kg"


def test_select_required_gap_uses_first_real_live_option():
    fallback = required_fallback_override(
        _field(
            attribute_key="colour",
            label="Colour",
            options=["Select One", "Orange", "Black"],
        )
    )

    assert fallback["values"] == ["Orange"]


def test_live_select_uses_only_enabled_option():
    fallback = required_fallback_override(
        _field(
            attribute_key="trimming_range",
            label="Trimming Range",
            options=["0.2 - 0.4 mm", "0.5 - 1 mm"],
            controls=[
                {
                    "name": "trimming_range_0_value",
                    "field_kind": "select",
                    "options": [
                        {"text": "0.2 - 0.4 mm", "value": "0.2 - 0.4 mm", "disabled": True},
                        {"text": "0.5 - 1 mm", "value": "0.5 - 1 mm", "disabled": False},
                    ],
                }
            ],
        )
    )

    assert fallback["values"] == ["0.5 - 1 mm"]


def test_selection_without_executable_option_fails_closed():
    field = _field(
        attribute_key="trimming_range",
        label="Trimming Range",
        options=["0.2 - 0.4 mm"],
        controls=[
            {
                "name": "trimming_range_0_value",
                "field_kind": "select",
                "options": [
                    {"text": "0.2 - 0.4 mm", "value": "0.2 - 0.4 mm", "disabled": True}
                ],
            }
        ],
    )

    with pytest.raises(RequiredOverrideError, match="selection 控件"):
        required_fallback_override(field)


def test_fallback_promotes_only_ai_missing_required_field():
    live = _field(attribute_key="colour", label="Colour", options=["Orange", "Black"])
    item = _blocked_item(live)
    plan = LiveFillPlan([item])

    result = apply_required_overrides(plan, [live], [required_fallback_override(live)])

    assert result["applied"] == 1
    assert result["sources"]["fallback"] == 1
    assert item.action == READY
    assert item.resolution.answer_values == ["Orange"]
    assert item.resolution.source_type == "fallback"
    assert item.resolution.source_reference == FALLBACK_SOURCE_REFERENCE


def test_ai_ready_is_never_replaced_by_fallback():
    live = _field(attribute_key="colour", label="Colour", options=["Orange", "Black"])
    item = _blocked_item(live)
    item.action = READY
    item.reason = "AI READY authoritative"
    item.resolution.status = RESOLVED
    item.resolution.answer = "Black"
    item.resolution.answer_values = ["Black"]
    item.resolution.source_type = "ai_decision"
    item.resolution.source_reference = "source:ai"
    item.resolution.eligible_for_autofill = True
    plan = LiveFillPlan([item])

    result = apply_required_overrides(plan, [live], [required_fallback_override(live)])

    assert result["applied"] == 0
    assert result["skipped_current_ready"] == 1
    assert item.action == READY
    assert item.resolution.answer_values == ["Black"]
    assert item.resolution.source_type == "ai_decision"


def test_explicit_user_value_still_wins_for_ai_missing_required_field():
    live = _field(attribute_key="colour", label="Colour", options=["Orange", "Black"])
    item = _blocked_item(live)
    plan = LiveFillPlan([item])

    result = apply_required_overrides(
        plan,
        [live],
        [{"field_id": field_id(live), "values": ["Black"], "source_type": "user"}],
    )

    assert result["applied"] == 1
    assert result["sources"]["user"] == 1
    assert item.action == READY
    assert item.resolution.answer_values == ["Black"]
    assert item.resolution.source_type == "user"
    assert item.resolution.source_reference == "user:required-override"


def test_persisted_fallback_is_recomputed_from_current_live_field():
    live = _field(attribute_key="model_name", label="Model Name")
    item = _blocked_item(live)
    plan = LiveFillPlan([item])
    persisted = {
        "field_id": field_id(live),
        "values": ["stale placeholder"],
        "source_type": "fallback",
    }

    result = apply_required_overrides(plan, [live], [persisted])

    assert result["applied"] == 1
    assert result["fallback_recomputed_live"] == 1
    assert item.resolution.answer_values == [FALLBACK_TEXT_VALUE]
    assert item.resolution.source_type == "fallback"


def test_gui_required_preflight_has_no_second_ai_but_has_fallback():
    assert "QProcess" not in GUI_SOURCE
    assert "makro_complete_required.py" not in GUI_SOURCE
    assert "required_fallback_override" in GUI_SOURCE
    assert "留空将自动填" in GUI_SOURCE
    assert "ai_calls=0" in GUI_SOURCE


def test_gui_manual_value_is_optional_and_reference_hud_never_gates_fill():
    support = __import__(
        "gui.required_input_support",
        fromlist=["RequiredInputSupport"],
    ).RequiredInputSupport
    merged = inspect.getsource(support._merged_overrides)
    sync = inspect.getsource(support._sync_button)
    request = inspect.getsource(support.request_start)

    assert "self.values" in GUI_SOURCE
    assert "required_fallback_override(field)" in merged
    assert "is_user_decision_business_field" not in merged
    assert "runtime_decisions" not in sync
    assert "runtime_decisions" not in request
    assert "HUD 待确认" not in request
    assert "not self._all_required_confirmed()" not in request
    assert ".text()" not in merged
