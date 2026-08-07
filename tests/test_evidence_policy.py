from __future__ import annotations

from app.answer_resolver import NEEDS_REVIEW
from app.evidence_pipeline import add_fact, source_policy
from app.resolution_engine import ResolutionPolicy, resolve_one
from app.source_bundle import ProductSourceBundle


def test_ai_synthesis_cannot_self_promote_to_autofill_confidence():
    bundle = ProductSourceBundle()
    add_fact(
        bundle,
        key="Image Resolution",
        value="1920x1080",
        source_type="ai_synthesis",
        source_reference="ai:source-backed-summary",
        confidence=0.99,
        note="model-reported confidence",
    )

    evidence = bundle.candidates(["Image Resolution"])[0]
    assert evidence.confidence == source_policy("ai_synthesis").max_confidence
    assert "confidence capped by source policy" in evidence.note

    record = resolve_one(
        {"attribute_key": "image_resolution", "label": "Image Resolution", "controls": []},
        bundle,
        policy=ResolutionPolicy(ai_auto_fill_min_confidence=0.92),
    )
    assert record.status == NEEDS_REVIEW
    assert record.eligible_for_autofill is False


def test_direct_product_image_can_still_cross_normal_autofill_threshold():
    bundle = ProductSourceBundle()
    add_fact(
        bundle,
        key="Image Resolution",
        value="1920x1080",
        source_type="product_image",
        source_reference="front.jpg:printed-spec",
        confidence=0.99,
    )

    evidence = bundle.candidates(["Image Resolution"])[0]
    assert evidence.confidence == source_policy("product_image").max_confidence
    assert evidence.confidence >= ResolutionPolicy().auto_fill_min_confidence
