from __future__ import annotations

from pathlib import Path

from app.listing_image_ranker import (
    _Candidate,
    _parse_ownership,
    build_listing_image_ownership_request,
)


def _candidate(image_id: str = "image_01") -> _Candidate:
    return _Candidate(
        image_id=image_id,
        path=Path(f"{image_id}.jpg"),
        sha256=f"sha-{image_id}",
        source_index=1,
    )


def test_ownership_contract_requires_pixel_facts_before_relationship_fields() -> None:
    request = build_listing_image_ownership_request(
        product_context={
            "name": "Dyson Airwrap HS05 Complete Long",
            "brand": "Dyson",
            "model": "HS05",
        },
        candidates=[_candidate()],
    )

    decision_schema = request["json_contract"]["properties"]["decisions"]["properties"][
        "image_01"
    ]
    required = decision_schema["required"]
    assert required[:3] == [
        "visual_subject",
        "visible_identity",
        "visible_configuration",
    ]
    assert required[3:] == ["classification", "confidence", "reason"]

    assert request["context"]["decision_protocol"] == (
        "pixel_visual_facts_then_target_relationship_v2"
    )
    prompt = request["prompt_instruction"].lower()
    rules = "\n".join(request["rules"]).lower()
    assert "from pixels before assigning classification" in prompt
    assert "do not copy target fields" in prompt
    assert "ocr/readable packaging text is one visual signal" in prompt
    assert "dom text" in rules
    assert "filenames or urls" in rules
    assert "only after recording visual facts" in rules


def test_parsed_ownership_preserves_compact_visual_evidence_for_gallery_and_report() -> None:
    candidate = _candidate()
    parsed = _parse_ownership(
        {
            "decisions": {
                "image_01": {
                    "visual_subject": "pink hair styling wand with multiple curling attachments",
                    "visible_identity": "Dyson; HS05 visible on packaging",
                    "visible_configuration": "pink/rose-gold long-barrel kit with storage case",
                    "classification": "EXACT_TARGET",
                    "confidence": 0.97,
                    "reason": "Visible product identity and configuration agree with the target sale unit.",
                }
            },
            "summary": "one exact target image",
        },
        [candidate],
    )

    decision = parsed.decisions["image_01"]
    assert decision.auto_eligible is True
    assert decision.visual_subject.startswith("pink hair styling wand")
    assert decision.visible_identity == "Dyson; HS05 visible on packaging"
    assert "storage case" in decision.visible_configuration
    assert decision.as_dict()["classification"] == "EXACT_TARGET"
    assert decision.as_dict()["visual_subject"] == decision.visual_subject


def test_program_eligibility_still_depends_only_on_relationship_classification() -> None:
    candidate = _candidate()
    parsed = _parse_ownership(
        {
            "decisions": {
                "image_01": {
                    "visual_subject": "hair styling tool",
                    "visible_identity": "Dyson",
                    "visible_configuration": "different visible colour/configuration",
                    "classification": "SAME_PRODUCT_OTHER_VARIANT",
                    "confidence": 0.99,
                    "reason": "The visible configuration conflicts with the exact target variant.",
                }
            },
            "summary": "variant rejected",
        },
        [candidate],
    )

    decision = parsed.decisions["image_01"]
    assert decision.visual_subject
    assert decision.confidence == 0.99
    assert decision.auto_eligible is False
    assert parsed.eligible_ids == ()
