from __future__ import annotations

from pathlib import Path

import pytest

from app.listing_image_ranker import (
    _Candidate,
    _parse_ownership,
    build_listing_image_ownership_request,
    build_listing_image_ranking_request,
    ListingImageRankingError,
)


def _candidate(image_id: str = "image_01") -> _Candidate:
    return _Candidate(
        image_id=image_id,
        path=Path(f"{image_id}.jpg"),
        sha256=f"sha-{image_id}",
        source_index=1,
    )


def test_ownership_contract_requires_pixel_facts_and_visual_fingerprint_proof_before_relationship() -> None:
    request = build_listing_image_ownership_request(
        product_context={
            "name": "Dyson Airwrap i.d Multi-Styler and Dryer",
            "brand": "Dyson",
            "model": "Airwrap i.d",
            "variant": "Ceramic Pink",
        },
        candidates=[_candidate()],
    )

    decision_schema = request["json_contract"]["properties"]["decisions"]["properties"][
        "image_01"
    ]
    required = decision_schema["required"]
    assert required[:5] == [
        "visual_subject",
        "visible_identity",
        "visible_configuration",
        "target_match_evidence",
        "target_identity_gaps",
    ]
    assert required[5:] == ["classification", "confidence", "reason"]

    assert request["context"]["decision_protocol"] == (
        "pixel_visual_facts_then_visual_fingerprint_identity_proof_v4"
    )
    prompt = request["prompt_instruction"].lower()
    rules = "\n".join(request["rules"]).lower()
    assert "from pixels" in prompt
    assert "target_match_evidence and target_identity_gaps before assigning classification" in prompt
    assert "not mandatory" in prompt
    assert "missing or unreadable model label" in prompt
    assert "colour alone" in prompt
    assert "generic product-family resemblance" in prompt
    assert "dom text" in rules
    assert "filenames or urls" in rules
    assert "visual fingerprint" in rules
    assert "catalog code does not have to be literally printed" in rules
    assert "empty visible_identity field does not by itself create an identity gap" in rules
    assert "do not list an unreadable brand/model label as a gap" in rules
    assert "matching brand logo proves only the brand" in rules
    assert "absence of conflict is not evidence of identity" in rules
    assert "do not use uncertain merely because a model code or logo is not readable" in rules


def test_gallery_recheck_does_not_require_readable_model_text_when_visual_fingerprint_is_sufficient() -> None:
    request = build_listing_image_ranking_request(
        product_context={
            "name": "Dyson Airwrap HS05",
            "brand": "Dyson",
            "model": "HS05",
            "variant": "Nickel / Copper",
        },
        candidates=[_candidate()],
    )

    prompt = request["prompt_instruction"].lower()
    rules = "\n".join(request["rules"]).lower()
    assert "readable logo/model text is not mandatory" in prompt
    assert "colour alone" in prompt
    assert "readable logo or model code is not required" in rules
    assert "do not reject a candidate merely because a textual model label is absent or unreadable" in rules
    assert "meaningful visual ambiguity remains" in rules


def test_parsed_ownership_preserves_visual_evidence_and_identity_proof_for_gallery_and_report() -> None:
    candidate = _candidate()
    parsed = _parse_ownership(
        {
            "decisions": {
                "image_01": {
                    "visual_subject": "pink hair styling wand with multiple curling attachments",
                    "visible_identity": "Dyson; Airwrap i.d visible on packaging",
                    "visible_configuration": "ceramic-pink multi-styler kit with storage case",
                    "target_match_evidence": "Airwrap i.d marking and ceramic-pink kit configuration visibly agree with the target.",
                    "target_identity_gaps": "",
                    "classification": "EXACT_TARGET",
                    "confidence": 0.97,
                    "reason": "Visible model identity and distinguishing configuration positively establish the target sale unit.",
                }
            },
            "summary": "one exact target image",
        },
        [candidate],
    )

    decision = parsed.decisions["image_01"]
    assert decision.auto_eligible is True
    assert decision.visual_subject.startswith("pink hair styling wand")
    assert decision.visible_identity == "Dyson; Airwrap i.d visible on packaging"
    assert "storage case" in decision.visible_configuration
    assert "Airwrap i.d marking" in decision.target_match_evidence
    assert decision.target_identity_gaps == ""
    assert decision.as_dict()["classification"] == "EXACT_TARGET"
    assert decision.as_dict()["target_match_evidence"] == decision.target_match_evidence


def test_program_eligibility_still_depends_only_on_relationship_classification() -> None:
    candidate = _candidate()
    parsed = _parse_ownership(
        {
            "decisions": {
                "image_01": {
                    "visual_subject": "hair styling tool",
                    "visible_identity": "Dyson; HS05 visible on packaging",
                    "visible_configuration": "rose-gold HS05 configuration",
                    "target_match_evidence": "Dyson brand is visible, but the target model/variant are not established.",
                    "target_identity_gaps": "Target Airwrap i.d model and Ceramic Pink variant are not established; visible HS05 conflicts.",
                    "classification": "SAME_PRODUCT_OTHER_VARIANT",
                    "confidence": 0.99,
                    "reason": "The visible model/configuration conflicts with the exact target identity.",
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


def test_parser_rejects_missing_positive_identity_proof_fields() -> None:
    candidate = _candidate()
    with pytest.raises(ListingImageRankingError, match="target_match_evidence"):
        _parse_ownership(
            {
                "decisions": {
                    "image_01": {
                        "visual_subject": "hair styling tool",
                        "visible_identity": "Dyson",
                        "visible_configuration": "pink styling tool",
                        "target_identity_gaps": "model and exact variant are visually ambiguous",
                        "classification": "UNCERTAIN",
                        "confidence": 0.6,
                        "reason": "Exact target identity is not positively established.",
                    }
                },
                "summary": "insufficient identity proof",
            },
            [candidate],
        )
