from __future__ import annotations

from pathlib import Path

import pytest

from app.ai_decisions import AIDecisionPacket, FieldDecision, READY as AI_READY, field_id
from app.evidence_contract import ProductIdentity
from app.fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem
from app.makro.marketplace_constraints import apply_makro_decision_constraints
from app.makro_dryrun import _fill_control
from app.required_overrides import (
    FALLBACK_NUMERIC_VALUE,
    FALLBACK_TEXT_VALUE,
    RequiredOverrideError,
    apply_required_overrides,
    required_fallback_override,
)
from app.resolution_types import MISSING, RESOLVED, ResolutionRecord


ROOT = Path(__file__).resolve().parents[1]
EXECUTOR_SOURCE = (ROOT / "makro_execute_listing.py").read_text(encoding="utf-8")


def _field(label: str, key: str, *, controls=None) -> dict[str, object]:
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": "Product Description (0/10)",
        "required": True,
        "multi_value": False,
        "options": [],
        "qualifier_options": [],
        "help_text": "",
        "context_text": "",
        "controls": list(controls or []),
    }


def _record(field: dict[str, object], *, resolved: bool = False) -> ResolutionRecord:
    return ResolutionRecord(
        attribute_key=str(field["attribute_key"]),
        label=str(field["label"]),
        status=RESOLVED if resolved else MISSING,
        answer="already ready" if resolved else None,
        answer_values=["already ready"] if resolved else [],
        qualifier=None,
        confidence=1.0 if resolved else 0.0,
        source_type="fixture" if resolved else None,
        source_reference="fixture" if resolved else None,
        evidence="fixture" if resolved else None,
        detail="fixture",
        eligible_for_autofill=resolved,
        preview_eligible=False,
        gate_reason="" if resolved else "ai_missing",
        question_category=str(field["section_heading"]),
        question_options=[],
    )


def _item(field: dict[str, object], *, action: str = BLOCKED) -> LiveFillPlanItem:
    return LiveFillPlanItem(
        attribute_key=str(field["attribute_key"]),
        label=str(field["label"]),
        section_heading=str(field["section_heading"]),
        required=True,
        action=action,
        reason="fixture",
        resolution=_record(field, resolved=action == READY),
    )


@pytest.mark.parametrize(
    ("label", "key", "control"),
    [
        ("Pick Pack SLA", "shipping_days", {"name": "shipping_days", "type": "number", "field_kind": "input"}),
        ("Air Flow Level", "air_flow_level", {"name": "air_flow_level", "type": "number", "field_kind": "input"}),
        ("Unfamiliar Metric", "mystery_metric", {"name": "mystery_metric", "role": "spinbutton", "field_kind": "input"}),
        ("Another Metric", "another_metric", {"name": "another_metric", "inputmode": "decimal", "field_kind": "input"}),
    ],
)
def test_numeric_fallback_uses_live_control_type_not_field_name(label, key, control):
    fallback = required_fallback_override(_field(label, key, controls=[control]))
    assert fallback["values"] == [FALLBACK_NUMERIC_VALUE]
    assert FALLBACK_NUMERIC_VALUE == "1"


def test_plain_text_control_still_uses_na_fallback():
    fallback = required_fallback_override(
        _field("Unfamiliar Text", "unfamiliar_text", controls=[{"name": "unfamiliar_text", "type": "text", "field_kind": "input"}])
    )
    assert fallback["values"] == [FALLBACK_TEXT_VALUE]
    assert FALLBACK_TEXT_VALUE == "N/A"


def test_stale_fallback_na_is_recomputed_from_current_number_control():
    planned = _field("Air Flow Level", "air_flow_level")
    current = _field(
        "Air Flow Level",
        "air_flow_level",
        controls=[{"name": "air_flow_level", "type": "number", "field_kind": "input"}],
    )
    stale = required_fallback_override(planned)
    assert stale["values"] == ["N/A"]

    item = _item(current)
    result = apply_required_overrides(
        LiveFillPlan([item]),
        [current],
        [stale],
        planned_fields=[planned],
    )

    assert result["fallback_recomputed_live"] == 1
    assert item.action == READY
    assert item.resolution.answer_values == ["1"]


def test_explicit_user_text_is_not_silently_accepted_for_number_control():
    current = _field(
        "Air Flow Level",
        "air_flow_level",
        controls=[{"name": "air_flow_level", "type": "number", "field_kind": "input"}],
    )
    item = _item(current)
    with pytest.raises(RequiredOverrideError, match="不是有限数字"):
        apply_required_overrides(
            LiveFillPlan([item]),
            [current],
            [{"field_id": field_id(current), "values": ["N/A"], "source_type": "user"}],
        )


def test_stale_override_does_not_replace_current_ready_decision():
    current = _field("Air Flow Level", "air_flow_level")
    item = _item(current, action=READY)
    result = apply_required_overrides(
        LiveFillPlan([item]),
        [current],
        [required_fallback_override(current)],
    )
    assert result["applied"] == 0
    assert result["skipped_current_ready"] == 1
    assert item.resolution.answer_values == ["already ready"]


def test_makro_model_name_constraint_removes_known_brand_without_inventing_value():
    field = _field("Model Name", "model_name")
    decision = FieldDecision(
        field_id=field_id(field),
        status=AI_READY,
        values=["Dexmary Air Purifier"],
    )
    packet = AIDecisionPacket(
        identity=ProductIdentity(brand="Dexmary"),
        schema_sha256="",
        source_manifest_sha256="",
        decisions=[decision],
    )

    summary = apply_makro_decision_constraints(packet, [field])

    assert summary["model_name_brand_removed"] == 1
    assert decision.status == AI_READY
    assert decision.values == ["Air Purifier"]


def test_makro_model_name_constraint_fails_closed_when_only_brand_remains():
    field = _field("Model Name", "model_name")
    decision = FieldDecision(
        field_id=field_id(field),
        status=AI_READY,
        values=["Dexmary"],
    )
    packet = AIDecisionPacket(
        identity=ProductIdentity(brand="Dexmary"),
        schema_sha256="",
        source_manifest_sha256="",
        decisions=[decision],
    )

    summary = apply_makro_decision_constraints(packet, [field])

    assert summary["model_name_blocked"] == 1
    assert decision.status == MISSING
    assert decision.values == []


class _SelectLocator:
    def __init__(self):
        self.first = self
        self.events: list[str] = []
        self.blurred = False

    def count(self):
        return 1

    def is_visible(self):
        return True

    def wait_for(self, state="visible"):
        assert state == "visible"

    def select_option(self, label=None, value=None):
        assert label == "ACTIVE" or value == "ACTIVE"

    def dispatch_event(self, event):
        self.events.append(event)

    def blur(self):
        self.blurred = True


class _SelectPage:
    def __init__(self, locator):
        self._locator = locator

    def locator(self, selector):
        return self._locator


def test_native_select_dispatches_framework_commit_events_even_for_visible_default():
    locator = _SelectLocator()
    page = _SelectPage(locator)
    control = {
        "name": "listing_status_0_value",
        "field_kind": "select",
        "path": "body > select",
        "selector_candidates": [],
    }

    _fill_control(page, control, "ACTIVE")

    assert locator.events == ["input", "change"]
    assert locator.blurred is True


def test_full_step3_incomplete_persistence_returns_nonzero_contract():
    assert 'completion.get("draft_persisted_complete")' in EXECUTOR_SOURCE
    assert 'completion.get("autofill_safe_complete")' in EXECUTOR_SOURCE
    assert "return 2" in EXECUTOR_SOURCE
    assert '"send_to_qc_clicked": False' in EXECUTOR_SOURCE
