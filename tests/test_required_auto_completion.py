from __future__ import annotations

from app.ai_decisions import field_id
from app.fill_plan import BLOCKED, LiveFillPlan, LiveFillPlanItem
from app.required_field_completion import (
    COMPLETION_SOURCE_REFERENCE,
    build_required_completion_request,
    parse_required_completion_response,
    required_targets,
)
from app.required_overrides import apply_required_overrides
from app.resolution_types import MISSING, ResolutionRecord


def _field(*, key: str = "colour", label: str = "Colour", options=None):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": "Product Description (0/10)",
        "required": True,
        "multi_value": False,
        "options": list(options or []),
        "qualifier_options": [],
        "help_text": "",
        "context_text": "",
        "controls": [],
    }


def _plan_payload(field):
    return {
        "items": [
            {
                "attribute_key": field["attribute_key"],
                "label": field["label"],
                "section_heading": field["section_heading"],
                "required": True,
                "action": "blocked",
                "reason": "ai_missing",
                "resolution": {"answer_values": [], "qualifier": "", "detail": "AI could not determine required value"},
            }
        ]
    }


def test_required_completion_targets_only_required_blocked_and_uses_guarded_prompt():
    live = _field(options=["Orange", "Black"])
    request = build_required_completion_request(
        _plan_payload(live),
        [live],
        product_url="https://example.invalid/product",
        grounded_sources=[{"source_id": "compact:web", "source_type": "compact_supplier_evidence", "kind": "text", "content": "Colour=Orange"}],
    )
    assert request["task"] == "complete_required_marketplace_fields_before_execution"
    assert request["evidence_policy"] == "best_effort"
    assert request["target_fields"][0]["options"] == ["Orange", "Black"]
    assert any("placeholder" in rule.casefold() for rule in request["rules"])
    assert any("ean/gtin/upc" in rule.casefold() for rule in request["rules"])


def test_required_completion_canonicalizes_exact_makro_option():
    live = _field(options=["Orange", "Black"])
    targets = required_targets(_plan_payload(live), [live])
    result = parse_required_completion_response(
        {"decisions": [{"field_id": field_id(live), "status": "ready", "values": ["orange"], "qualifier": "", "confidence": 0.91, "reason": "consistent with product evidence", "basis": "inference", "source_id": ""}], "summary": "done"},
        targets,
        [live],
        allowed_source_ids=["compact:web"],
    )
    assert result["ready"] == 1
    assert result["unresolved_count"] == 0
    assert result["overrides"][0]["values"] == ["Orange"]
    assert result["overrides"][0]["source_type"] == "model"
    assert result["overrides"][0]["source_reference"] == COMPLETION_SOURCE_REFERENCE
    assert result["overrides"][0]["confidence"] <= 0.82


def test_identifier_like_required_value_requires_explicit_source_basis():
    live = _field(key="model_number", label="Model Number")
    targets = required_targets(_plan_payload(live), [live])
    raw = {"decisions": [{"field_id": field_id(live), "status": "ready", "values": ["ABC-123"], "qualifier": "", "confidence": 0.7, "reason": "typical model number", "basis": "inference", "source_id": ""}], "summary": ""}
    rejected = parse_required_completion_response(raw, targets, [live], allowed_source_ids=["compact:web"])
    assert rejected["ready"] == 0
    raw["decisions"][0]["basis"] = "source"
    raw["decisions"][0]["source_id"] = "compact:web"
    accepted = parse_required_completion_response(raw, targets, [live], allowed_source_ids=["compact:web"])
    assert accepted["ready"] == 1


def test_model_completion_still_passes_required_override_hard_guards_and_provenance():
    live = _field(options=["Orange", "Black"])
    record = ResolutionRecord(attribute_key="colour", label="Colour", status=MISSING, answer=None, answer_values=[], qualifier=None, confidence=0.0, source_type=None, source_reference=None, evidence=None, detail="missing", eligible_for_autofill=False, gate_reason="ai_missing", question_category="Product Description (0/10)", question_options=["Orange", "Black"])
    item = LiveFillPlanItem(attribute_key="colour", label="Colour", section_heading="Product Description (0/10)", required=True, action=BLOCKED, reason="missing", resolution=record)
    plan = LiveFillPlan([item])
    summary = apply_required_overrides(plan, [live], [{"field_id": field_id(live), "values": ["Orange"], "source_type": "model", "source_reference": COMPLETION_SOURCE_REFERENCE, "confidence": 0.74, "reason": "targeted completion"}])
    assert summary["sources"]["model"] == 1
    assert item.action == "ready"
    assert item.resolution.source_type == "model"
    assert item.resolution.source_reference == COMPLETION_SOURCE_REFERENCE
    assert item.resolution.confidence == 0.74
