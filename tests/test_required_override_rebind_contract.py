from __future__ import annotations

import inspect

import pytest

import makro_execute_listing
from app.ai_decisions import field_id
from app.fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem
from app.live_schema import assert_live_schema_matches, schema_field_signature
from app.required_overrides import (
    RequiredOverrideError,
    apply_required_overrides,
    required_fallback_override,
    required_override_binding,
)
from app.resolution_types import MISSING, ResolutionRecord


def _field(**updates):
    field = {
        "attribute_key": "model_name",
        "label": "Model Name",
        "section_heading": "Product Description (0/10)",
        "required": True,
        "multi_value": False,
        "options": [],
        "qualifier_options": [],
        "help_text": "planning help text",
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


def test_old_override_field_id_rebinds_when_production_schema_identity_is_unchanged():
    planned = _field(help_text="serialized planning help")
    current = _field(help_text="current DOM help changed")

    # This is the exact production boundary from the real failure: the schema
    # drift gate accepts the fields, while the presentation-sensitive field id
    # can still differ.
    assert_live_schema_matches([planned], [current])
    assert schema_field_signature(planned) == schema_field_signature(current)
    assert field_id(planned) != field_id(current)

    item = _blocked_item(current)
    plan = LiveFillPlan([item])
    old_override = {
        "field_id": field_id(planned),
        "values": ["N/A"],
        "source_type": "fallback",
    }

    result = apply_required_overrides(
        plan,
        [current],
        [old_override],
        planned_fields=[planned],
    )

    assert result["applied"] == 1
    assert result["rebound_by_schema_signature"] == 1
    assert result["field_ids"] == [field_id(current)]
    assert item.action == READY
    assert item.resolution.answer_values == ["N/A"]


def test_new_fallback_persists_stable_schema_signature():
    field = _field()
    fallback = required_fallback_override(field)
    binding = required_override_binding(field)

    assert fallback["field_id"] == binding["field_id"]
    assert fallback["schema_signature"] == binding["schema_signature"]
    assert len(fallback["schema_signature"]) == 7


def test_stable_schema_rebind_remains_fail_closed_when_current_match_is_ambiguous():
    planned = _field(help_text="serialized planning help")
    current_a = _field(help_text="current DOM help A")
    current_b = _field(help_text="current DOM help B")
    plan = LiveFillPlan([_blocked_item(current_a)])

    override = {
        **required_override_binding(planned),
        "values": ["N/A"],
        "source_type": "fallback",
    }

    with pytest.raises(RequiredOverrideError, match="stable_matches=2"):
        apply_required_overrides(
            plan,
            [current_a, current_b],
            [override],
            planned_fields=[planned],
        )


def test_executor_passes_planned_schema_to_required_override_rebind():
    source = inspect.getsource(makro_execute_listing.main)

    assert "assert_live_schema_matches(planned_live_fields, semantic_fields)" in source
    assert "planned_fields=planned_live_fields" in source
    assert source.index("assert_live_schema_matches(planned_live_fields, semantic_fields)") < source.index(
        "planned_fields=planned_live_fields"
    )