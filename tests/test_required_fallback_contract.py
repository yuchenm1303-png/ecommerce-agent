from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.ai_decisions import field_id
from app.fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem
from app.required_overrides import (
    RequiredOverrideError,
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


@pytest.mark.parametrize(
    "field",
    [
        _field(),
        _field(attribute_key="model_name", label="Model Name"),
        _field(attribute_key="type", label="Type", options=["Select One", "Room", "Car"]),
        _field(attribute_key="package_weight", label="Package Weight", qualifier_options=["kg", "g"]),
        _field(attribute_key="colour", label="Colour", options=["Select One", "Orange", "Black"]),
    ],
)
def test_python_never_synthesizes_required_product_answers(field):
    with pytest.raises(RequiredOverrideError, match="自动 N/A / 1 / 首选项兜底已禁用"):
        required_fallback_override(field)


def test_explicit_user_decision_can_resolve_ai_missing_required_field():
    live = _field(attribute_key="colour", label="Colour", options=["Orange", "Black"])
    item = _blocked_item(live)
    plan = LiveFillPlan([item])

    result = apply_required_overrides(
        plan,
        [live],
        [{"field_id": field_id(live), "values": ["Purple"], "source_type": "user"}],
    )

    assert result["applied"] == 1
    assert item.action == READY
    assert item.resolution.answer_values == ["Purple"]
    assert item.resolution.source_type == "user"
    assert item.resolution.source_reference == "user:required-override"


def test_legacy_automatic_override_cannot_promote_ai_missing():
    live = _field(attribute_key="colour", label="Colour", options=["Orange", "Black"])
    item = _blocked_item(live)
    plan = LiveFillPlan([item])

    result = apply_required_overrides(
        plan,
        [live],
        [{"field_id": field_id(live), "values": ["Orange"], "source_type": "fallback"}],
    )

    assert result["applied"] == 0
    assert result["ignored_automatic"] == 1
    assert item.action == BLOCKED


def test_gui_required_preflight_has_no_second_ai_or_synthetic_fallback():
    assert "QProcess" not in GUI_SOURCE
    assert "makro_complete_required.py" not in GUI_SOURCE
    assert "required_fallback_override" not in GUI_SOURCE
    assert "自动兜底 0" in GUI_SOURCE
    assert "ai_calls=0" in GUI_SOURCE


def test_gui_manual_value_is_mandatory_for_non_ready_required_field():
    support = __import__(
        "gui.required_input_support",
        fromlist=["RequiredInputSupport"],
    ).RequiredInputSupport
    merged = inspect.getsource(support._merged_overrides)
    sync = inspect.getsource(support._sync_button)
    request = inspect.getsource(support.request_start)

    assert "self.values" in GUI_SOURCE
    assert "source_type\": \"user" in merged
    assert "required_fallback_override" not in merged
    assert "confirmed == total" in sync
    assert "not self._all_required_confirmed()" in request
    assert ".text()" not in merged
