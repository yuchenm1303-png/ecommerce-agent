from __future__ import annotations

import inspect

import pytest

from app.ai_decisions import field_id
from app.fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem
from app.makro.execution import fill_one_section, run_photos
from app.required_overrides import RequiredOverrideError, apply_required_overrides
from app.resolution_types import MISSING, ResolutionRecord


def _blocked_item(*, required: bool = True) -> LiveFillPlanItem:
    record = ResolutionRecord(
        attribute_key="colour",
        label="Colour",
        status=MISSING,
        answer=None,
        answer_values=[],
        qualifier=None,
        confidence=0.0,
        source_type=None,
        source_reference=None,
        evidence=None,
        detail="AI could not determine required value",
        eligible_for_autofill=False,
        gate_reason="ai_missing",
        question_category="Product Description (0/10)",
        question_options=["Orange", "Black"],
    )
    return LiveFillPlanItem(
        attribute_key="colour",
        label="Colour",
        section_heading="Product Description (0/10)",
        required=required,
        action=BLOCKED,
        reason=record.detail,
        resolution=record,
    )


def _live_field() -> dict[str, object]:
    return {
        "attribute_key": "colour",
        "label": "Colour",
        "section_heading": "Product Description (0/10)",
        "required": True,
        "multi_value": False,
        "options": ["Select One", "Orange", "Black"],
        "qualifier_options": [],
        "help_text": "",
        "context_text": "",
        "controls": [],
    }


def test_explicit_user_value_promotes_only_unresolved_required_field_to_ready():
    live = _live_field()
    item = _blocked_item()
    plan = LiveFillPlan([item])

    result = apply_required_overrides(
        plan,
        [live],
        [{"field_id": field_id(live), "values": ["Orange"]}],
    )

    assert result["applied"] == 1
    assert item.action == READY
    assert item.resolution.answer_values == ["Orange"]
    assert item.resolution.source_type == "user"
    assert item.resolution.source_reference == "user:required-field-input"
    assert item.resolution.eligible_for_autofill is True


def test_user_required_value_still_must_match_live_makro_option():
    live = _live_field()
    plan = LiveFillPlan([_blocked_item()])

    with pytest.raises(RequiredOverrideError, match="Colour"):
        apply_required_overrides(
            plan,
            [live],
            [{"field_id": field_id(live), "values": ["Purple"]}],
        )


def test_user_override_cannot_replace_optional_or_already_resolved_decision():
    live = _live_field()
    optional = _blocked_item(required=False)
    plan = LiveFillPlan([optional])
    with pytest.raises(RequiredOverrideError, match="不是 required"):
        apply_required_overrides(
            plan,
            [live],
            [{"field_id": field_id(live), "values": ["Orange"]}],
        )


def test_production_ready_executor_has_no_existing_value_second_gate():
    source = inspect.getsource(fill_one_section)
    assert "_has_existing_value" not in source
    assert "skipped_existing\"] +=" not in source
    assert 'report["writes_attempted"] += 1' in source


def test_production_photo_path_requires_complete_requested_set_and_rediscovers_card():
    source = inspect.getsource(run_photos)
    assert 'int(report["staged"]) != requested' in source
    assert 'report["status"] = "incomplete_upload"' in source
    assert "_wait_for_file_input(adapter" in source
    assert "expected_added=requested" in source
