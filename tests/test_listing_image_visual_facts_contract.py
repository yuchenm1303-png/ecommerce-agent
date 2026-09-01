from __future__ import annotations

from pathlib import Path

import pytest

from app.listing_image_ranker import (
    _Candidate,
    _parse_ownership,
    _parse_ranking,
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


def test_ownership_contract_is_conflict_first_before_positive_proof_and_classification() -> None:
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
    assert decision_schema["required"] == [
        "visual_subject",
        "visible_identity",
        "visible_configuration",
        "target_conflicts",
        "target_match_evidence",
        "target_identity_gaps",
        "classification",
        "confidence",
        "reason",
    ]
    assert request["context"]["decision_protocol"] == (
        "pixel_facts_conflict_audit_positive_proof_classification_v5"
    )

    prompt = request["prompt_instruction"].lower()
    rules = "\n".join(request["rules"]).lower()
    assert "target_conflicts before target_match_evidence" in prompt
    assert "can never be overridden" in prompt
    assert "veto priority" in rules
    assert "positive similarities must never cancel" in rules
    assert "do not collapse distinct sellable colourways" in rules
    assert "missing or unreadable logo/model text alone is not a gap" in rules
    assert "absence of conflict is not evidence of identity" in rules
    assert "dom text" in rules
    assert "filenames or urls" in rules


def test_gallery_is_an_independent_conflict_first_reinspection() -> None:
    request = build_listing_image_ranking_request(
        product_context={
            "name": "Dyson Airwrap HS05",
            "brand": "Dyson",
            "model": "HS05",
            "variant": "Nickel / Copper",
        },
        candidates=[_candidate()],
    )

    decision_schema = request["json_contract"]["properties"]["decisions"]["properties"][
        "image_01"
    ]
    assert decision_schema["required"][:3] == [
        "target_conflicts",
        "target_match_evidence",
        "target_identity_gaps",
    ]
    assert request["context"]["decision_protocol"] == (
        "independent_conflict_first_gallery_verification_v1"
    )
    system = request["system_instruction"].lower()
    prompt = request["prompt_instruction"].lower()
    rules = "\n".join(request["rules"]).lower()
    assert "ownership decisions are prior-stage context, not proof" in system
    assert "target_conflicts first" in prompt
    assert "material visible target conflict requires selected=false" in prompt
    assert "never inherit the ownership conclusion as evidence" in rules
    assert "different sellable variant/colourway/material finish/configuration" in rules
    assert "different product line or product form" in rules


def test_parsed_ownership_preserves_separate_conflict_proof_and_ambiguity_audit() -> None:
    candidate = _candidate()
    parsed = _parse_ownership(
        {
            "decisions": {
                "image_01": {
                    "visual_subject": "pink hair styling wand with multiple curling attachments",
                    "visible_identity": "Dyson; Airwrap i.d visible on packaging",
                    "visible_configuration": "ceramic-pink multi-styler kit with storage case",
                    "target_conflicts": "",
                    "target_match_evidence": "Airwrap i.d marking and ceramic-pink kit configuration visibly establish the target.",
                    "target_identity_gaps": "",
                    "classification": "EXACT_TARGET",
                    "confidence": 0.97,
                    "reason": "Exact target identity and configuration are positively established with no conflict.",
                }
            },
            "summary": "one exact target image",
        },
        [candidate],
    )

    decision = parsed.decisions["image_01"]
    assert decision.auto_eligible is True
    assert decision.target_conflicts == ""
    assert "Airwrap i.d marking" in decision.target_match_evidence
    assert decision.target_identity_gaps == ""
    assert decision.as_dict()["target_conflicts"] == ""


def test_ownership_parser_rejects_auto_eligible_decision_that_declares_visible_conflict() -> None:
    candidate = _candidate()
    with pytest.raises(ListingImageRankingError, match="target_conflicts"):
        _parse_ownership(
            {
                "decisions": {
                    "image_01": {
                        "visual_subject": "rose-gold hair styler",
                        "visible_identity": "Dyson Airwrap family",
                        "visible_configuration": "rose-gold/copper finish",
                        "target_conflicts": "Visible rose-gold/copper finish conflicts with target Ceramic Pink variant.",
                        "target_match_evidence": "Airwrap-family body form resembles the target family.",
                        "target_identity_gaps": "",
                        "classification": "EXACT_TARGET",
                        "confidence": 0.9,
                        "reason": "synthetic inconsistent decision",
                    }
                },
                "summary": "inconsistent",
            },
            [candidate],
        )


def test_gallery_parser_rejects_selected_decision_that_declares_visible_conflict() -> None:
    candidate = _candidate()
    with pytest.raises(ListingImageRankingError, match="target_conflicts"):
        _parse_ranking(
            {
                "selected_image_ids": ["image_01"],
                "decisions": {
                    "image_01": {
                        "target_conflicts": "Visible product form is Airstrait, not the target Airwrap i.d.",
                        "target_match_evidence": "Dyson brand and hair-tool category are visible.",
                        "target_identity_gaps": "",
                        "selected": True,
                        "reason": "synthetic inconsistent decision",
                    }
                },
                "summary": "inconsistent",
            },
            [candidate],
        )


def test_ownership_parser_keeps_non_text_visual_fingerprint_valid_when_no_conflict_or_gap() -> None:
    candidate = _candidate()
    parsed = _parse_ownership(
        {
            "decisions": {
                "image_01": {
                    "visual_subject": "multi-styler with curling barrels and dryer head",
                    "visible_identity": "",
                    "visible_configuration": "nickel body, copper accents, matching attachment layout",
                    "target_conflicts": "",
                    "target_match_evidence": "Distinctive multi-styler geometry, attachment set, nickel body and copper accents jointly establish the target visual fingerprint.",
                    "target_identity_gaps": "",
                    "classification": "EXACT_TARGET",
                    "confidence": 0.92,
                    "reason": "Non-text visual fingerprint is sufficiently discriminative.",
                }
            },
            "summary": "exact without readable model text",
        },
        [candidate],
    )

    assert parsed.eligible_ids == ("image_01",)
