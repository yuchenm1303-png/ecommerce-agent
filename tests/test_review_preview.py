from __future__ import annotations

import pytest

from app.ai_decisions import (
    CONFLICT,
    READY as AI_READY,
    REVIEW,
    AIDecisionPacket,
    DecisionCitation,
    FieldDecision,
    field_id,
)
from app.evidence_contract import ProductIdentity
from app.fill_plan import (
    BLOCKED,
    GATE_AI_CONFLICT,
    GATE_AI_REVIEW,
    GATE_HARD_FIELD_CONSTRAINT,
    build_live_fill_plan,
)
from app.resolution_types import NEEDS_REVIEW, RESOLVED
from app.review_preview import ReviewPreviewBlocked, execution_answer_for_item
from app.source_bundle import ProductSourceBundle


def field(
    key: str,
    label: str,
    *,
    section: str = "Additional Description",
    control: dict | None = None,
):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
        "required": False,
        "multi_value": False,
        "options": [],
        "controls": [
            control
            or {
                "name": f"{key}_0_value",
                "field_kind": "input",
                "type": "text",
            }
        ],
    }


def decision(target, status, value="M8"):
    return FieldDecision(
        field_id=field_id(target),
        status=status,
        values=[value] if value else [],
        confidence=0.72 if status == REVIEW else 0.95,
        citations=(
            [DecisionCitation(source_reference="image:001", evidence_text="visible evidence")]
            if value
            else []
        ),
        reason="AI decision",
    )


def packet(decisions):
    return AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1"),
        schema_sha256="",
        source_manifest_sha256="",
        decisions=decisions,
    )


def test_ai_review_is_previewable_but_not_autofillable():
    target = field("model_name", "Model Name")
    plan = build_live_fill_plan(
        packet([decision(target, REVIEW)]),
        [target],
        ProductSourceBundle(),
    )
    item = plan.items[0]

    assert item.action == BLOCKED
    assert item.resolution.status == NEEDS_REVIEW
    assert item.resolution.eligible_for_autofill is False
    assert item.resolution.preview_eligible is True
    assert item.resolution.gate_reason == GATE_AI_REVIEW
    assert plan.summary()["preview_eligible"] == 1

    execution = execution_answer_for_item(
        item,
        include_review_candidates=True,
    )
    assert execution.status == RESOLVED
    assert execution.answer_values == ["M8"]
    assert item.resolution.status == NEEDS_REVIEW


def test_review_candidate_requires_explicit_preview_opt_in():
    target = field("model_name", "Model Name")
    item = build_live_fill_plan(
        packet([decision(target, REVIEW)]),
        [target],
        ProductSourceBundle(),
    ).items[0]

    with pytest.raises(ReviewPreviewBlocked):
        execution_answer_for_item(
            item,
            include_review_candidates=False,
        )


def test_ai_conflict_never_becomes_preview_eligible():
    target = field("model_name", "Model Name")
    conflict = decision(target, CONFLICT, value="")
    item = build_live_fill_plan(
        packet([conflict]),
        [target],
        ProductSourceBundle(),
    ).items[0]

    assert item.action == BLOCKED
    assert item.resolution.preview_eligible is False
    assert item.resolution.gate_reason == GATE_AI_CONFLICT


def test_hard_numeric_constraint_failure_never_becomes_preview_eligible():
    numeric = {
        "name": "lens_size_0_value",
        "field_kind": "input",
        "type": "number",
        "min": "0",
        "max": "10",
    }
    target = field("lens_size", "Lens Size", control=numeric)
    item = build_live_fill_plan(
        packet([decision(target, AI_READY, value="20")]),
        [target],
        ProductSourceBundle(),
    ).items[0]

    assert item.action == BLOCKED
    assert item.resolution.status == NEEDS_REVIEW
    assert item.resolution.gate_reason == GATE_HARD_FIELD_CONSTRAINT
    assert item.resolution.preview_eligible is False
    assert "最大值" in item.resolution.detail
