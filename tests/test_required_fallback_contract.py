from __future__ import annotations

import inspect
from pathlib import Path

from app.fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem
from app.required_overrides import (
    FALLBACK_NUMERIC_VALUE,
    FALLBACK_SOURCE_REFERENCE,
    FALLBACK_TEXT_VALUE,
    apply_required_overrides,
    required_fallback_override,
)
from app.resolution_types import MISSING, ResolutionRecord


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
        detail="normal resolver left this required field unresolved",
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


def test_ordinary_free_text_required_gap_uses_na_without_ai():
    fallback = required_fallback_override(_field())

    assert fallback["values"] == [FALLBACK_TEXT_VALUE]
    assert FALLBACK_TEXT_VALUE == "N/A"
    assert fallback["source_type"] == "fallback"


def test_model_name_missing_value_keeps_legacy_required_fallback():
    fallback = required_fallback_override(
        _field(attribute_key="model_name", label="Model Name")
    )

    assert fallback["values"] == [FALLBACK_TEXT_VALUE]
    assert fallback["source_type"] == "fallback"


def test_title_contributor_missing_value_keeps_legacy_live_option_fallback():
    fallback = required_fallback_override(
        _field(
            attribute_key="type",
            label="Type",
            options=["Select One", "Room", "Car"],
            context_text="Attributes that can make up title",
        )
    )

    assert fallback["values"] == ["Room"]
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


def test_select_required_gap_takes_first_real_live_option_not_select_one():
    fallback = required_fallback_override(
        _field(
            attribute_key="colour",
            label="Colour",
            options=["Select One", "Orange", "Black"],
        )
    )

    assert fallback["values"] == ["Orange"]


def test_deterministic_fallback_is_promoted_to_ready_through_existing_hard_guard():
    live = _field(
        attribute_key="colour",
        label="Colour",
        options=["Select One", "Orange", "Black"],
    )
    item = _blocked_item(live)
    plan = LiveFillPlan([item])

    result = apply_required_overrides(plan, [live], [required_fallback_override(live)])

    assert result["applied"] == 1
    assert result["sources"]["fallback"] == 1
    assert item.action == READY
    assert item.resolution.answer_values == ["Orange"]
    assert item.resolution.source_type == "fallback"
    assert item.resolution.source_reference == FALLBACK_SOURCE_REFERENCE
    assert item.resolution.eligible_for_autofill is True


def test_model_name_fallback_is_recomputed_and_promoted_through_existing_guard():
    live = _field(attribute_key="model_name", label="Model Name")
    item = _blocked_item(live)
    plan = LiveFillPlan([item])
    stale = {
        "field_id": __import__("app.ai_decisions", fromlist=["field_id"]).field_id(live),
        "values": ["N/A"],
        "source_type": "fallback",
    }

    result = apply_required_overrides(plan, [live], [stale])

    assert result["applied"] == 1
    assert item.action == READY
    assert item.resolution.answer_values == [FALLBACK_TEXT_VALUE]
    assert item.resolution.source_type == "fallback"


def test_gui_required_preflight_has_no_second_ai_process_or_cli():
    assert "QProcess" not in GUI_SOURCE
    assert "makro_complete_required.py" not in GUI_SOURCE
    assert "required-ai" not in GUI_SOURCE
    assert "AI 补齐必填项" not in GUI_SOURCE
    assert "required_fallback_override" in GUI_SOURCE
    assert "不会再调用 AI" in GUI_SOURCE
    assert "ai_calls=0" in GUI_SOURCE


def test_gui_manual_value_is_optional_and_empty_value_can_get_fallback():
    assert 'editor.setPlaceholderText(f"必填 · 留空将自动填 {fallback_text}")' in GUI_SOURCE
    assert 'if value:' in inspect.getsource(__import__("gui.required_input_support", fromlist=["RequiredInputSupport"]).RequiredInputSupport._merged_overrides)
    assert "required_fallback_override(field)" in GUI_SOURCE


def test_gui_required_business_state_never_reads_qlineedit_lifetime():
    support = __import__(
        "gui.required_input_support",
        fromlist=["RequiredInputSupport"],
    ).RequiredInputSupport
    merged = inspect.getsource(support._merged_overrides)
    sync = inspect.getsource(support._sync_button)
    request = inspect.getsource(support.request_start)

    assert "self.values" in GUI_SOURCE
    assert "for identifier, field in self.fields.items()" in merged
    assert ".text()" not in merged
    assert "editor.text()" not in sync
    assert "editor.text()" not in request
    assert "self._manual_count()" in sync
    assert "self._manual_count()" in request
