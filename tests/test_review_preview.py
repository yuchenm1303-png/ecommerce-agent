from __future__ import annotations

import pytest

from app.answer_resolver import CONFLICT, NEEDS_REVIEW, RESOLVED
from app.fill_plan import BLOCKED, build_live_fill_plan
from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.resolution_engine import GATE_FIELD_CONSTRAINT, GATE_LOW_CONFIDENCE, resolve_one
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
        "controls": [
            control
            or {
                "name": f"{key}_0_value",
                "field_kind": "input",
                "type": "text",
            }
        ],
    }


def catalog(question: str = "Model Name") -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[QuestionRecord(number="1", question=question)],
    )


def add_evidence(
    bundle: ProductSourceBundle,
    *,
    key: str,
    value: str,
    source_type: str = "ai_synthesis",
    reference: str = "image:001",
    priority: int = 90,
    confidence: float = 0.84,
):
    bundle.add_evidence(
        key=key,
        value=value,
        source_type=source_type,
        source_reference=reference,
        priority=priority,
        confidence=confidence,
        evidence_text=f"{key}: {value}",
    )


def test_low_confidence_is_previewable_but_not_autofillable():
    bundle = ProductSourceBundle()
    add_evidence(bundle, key="Model Name", value="M8")

    plan = build_live_fill_plan(
        catalog(),
        [field("model_name", "Model Name")],
        bundle,
    )
    item = plan.items[0]

    assert item.action == BLOCKED
    assert item.resolution.status == NEEDS_REVIEW
    assert item.resolution.eligible_for_autofill is False
    assert item.resolution.preview_eligible is True
    assert item.resolution.gate_reason == GATE_LOW_CONFIDENCE
    assert plan.summary()["preview_eligible"] == 1

    execution = execution_answer_for_item(
        item,
        include_review_candidates=True,
    )
    assert execution.status == RESOLVED
    assert execution.answer_values == ["M8"]
    assert item.resolution.status == NEEDS_REVIEW


def test_review_candidate_requires_explicit_preview_opt_in():
    bundle = ProductSourceBundle()
    add_evidence(bundle, key="Model Name", value="M8")
    item = build_live_fill_plan(
        catalog(),
        [field("model_name", "Model Name")],
        bundle,
    ).items[0]

    with pytest.raises(ReviewPreviewBlocked):
        execution_answer_for_item(
            item,
            include_review_candidates=False,
        )


def test_conflict_never_becomes_preview_eligible():
    bundle = ProductSourceBundle()
    add_evidence(bundle, key="Model Name", value="M8", reference="image:001")
    add_evidence(bundle, key="Model Name", value="M9", reference="supplier:001")

    record = resolve_one(
        field("model_name", "Model Name"),
        bundle,
        question=QuestionRecord(number="1", question="Model Name"),
    )

    assert record.status == CONFLICT
    assert record.eligible_for_autofill is False
    assert record.preview_eligible is False


def test_field_constraint_failure_never_becomes_preview_eligible():
    bundle = ProductSourceBundle()
    add_evidence(bundle, key="Lens Size", value="20")
    numeric = {
        "name": "lens_size_0_value",
        "field_kind": "input",
        "type": "number",
        "min": "0",
        "max": "10",
    }

    record = resolve_one(
        field("lens_size", "Lens Size", control=numeric),
        bundle,
        question=QuestionRecord(number="1", question="Lens Size"),
    )

    assert record.status == NEEDS_REVIEW
    assert record.gate_reason == GATE_FIELD_CONSTRAINT
    assert record.preview_eligible is False
    assert "最大值" in record.detail


def test_unmatched_semantic_collision_cannot_enter_review_preview():
    bundle = ProductSourceBundle()
    add_evidence(bundle, key="Height", value="7")

    plan = build_live_fill_plan(
        catalog("Model Name"),
        [
            field(
                "height",
                "Length",
                section="Price, Stock and Shipping Information",
            )
        ],
        bundle,
    )
    item = plan.items[0]

    assert item.question == ""
    assert item.action == BLOCKED
    assert item.resolution.preview_eligible is False
    assert item.resolution.gate_reason == "unmatched_live_field"
