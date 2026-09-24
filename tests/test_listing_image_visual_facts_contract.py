from __future__ import annotations

from pathlib import Path

import pytest

from app.listing_image_ranker import (
    ImageVisualFacts,
    ListingImageRankingError,
    _Candidate,
    _ParsedOwnership,
    _ParsedVisualFacts,
    _parse_ownership,
    build_listing_image_ownership_request,
    build_listing_image_ranking_request,
    build_listing_image_visual_facts_request,
)


def _candidate(image_id: str = "image_01") -> _Candidate:
    return _Candidate(
        image_id=image_id,
        path=Path(f"{image_id}.jpg"),
        sha256=f"sha-{image_id}",
        source_index=1,
    )


def _facts(
    *,
    colour: str = "silver/nickel body with metallic copper accents",
    guess: str = "Dyson Airwrap-style multi-styler; exact catalog model uncertain",
    uncertainty: str = "exact catalog model not readable",
) -> _ParsedVisualFacts:
    return _ParsedVisualFacts(
        facts={
            "image_01": ImageVisualFacts(
                image_id="image_01",
                visual_subject="multi-styler with cylindrical handle and attachments",
                readable_identity="Dyson",
                raw_colour_materials=colour,
                design_configuration="cylindrical handle, airflow head and curling attachments",
                neutral_product_guess=guess,
                visual_uncertainty=uncertainty,
                presentation_quality="clear complete hero-style product view",
            )
        },
        summary="blind facts",
    )


def test_blind_visual_perception_request_contains_no_target_identity() -> None:
    request = build_listing_image_visual_facts_request(candidates=[_candidate()])

    assert request["task"] == "observe_supplier_listing_images_blind"
    assert request["context"]["decision_protocol"] == "target_blind_pixel_observation_v1"
    assert "target_product" not in request["context"]
    assert request["target_fields"] == []
    assert len(request["grounded_sources"]) == 1

    prompt = request["prompt_instruction"].lower()
    rules = "\n".join(request["rules"]).lower()
    assert "do not invent or normalize a marketing colourway/variant name" in prompt
    assert "ceramic pink" in prompt
    assert "only when that exact wording is readable" in prompt
    assert "no target_product" in rules
    assert "raw_colour_materials uses literal visual descriptors" in rules
    assert "must not manufacture a variant name" in rules


def test_target_comparator_sees_only_frozen_blind_facts_not_candidate_pixels() -> None:
    facts = _facts()
    request = build_listing_image_ownership_request(
        product_context={
            "name": "Dyson Airwrap HS05",
            "brand": "Dyson",
            "model": "HS05",
            "variant": "Nickel / Copper",
        },
        candidates=[_candidate()],
        visual_facts=facts,
    )

    assert request["task"] == "compare_blind_image_facts_to_target"
    assert request["context"]["decision_protocol"] == (
        "frozen_blind_facts_target_comparison_v6"
    )
    assert request["grounded_sources"] == []
    assert request["context"]["blind_visual_facts"]["image_01"] == (
        facts.facts["image_01"].as_dict()
    )

    rules = "\n".join(request["rules"]).lower()
    assert "cannot inspect pixels in this stage" in rules
    assert "generic visual terms are not exact seller-variant proof" in rules
    assert "cannot be silently renamed" in rules
    assert "compare them as frozen observations" in rules


def test_ownership_parser_attaches_frozen_blind_facts_without_rewriting_them() -> None:
    facts = _facts()
    parsed = _parse_ownership(
        {
            "decisions": {
                "image_01": {
                    "target_conflicts": "",
                    "target_match_evidence": (
                        "Frozen nickel/silver body, copper accents and distinctive multi-styler "
                        "design jointly support the target."
                    ),
                    "target_identity_gaps": "",
                    "classification": "EXACT_TARGET",
                    "confidence": 0.94,
                    "reason": "Exact target is sufficiently established from frozen facts.",
                }
            },
            "summary": "exact",
        },
        [_candidate()],
        facts,
    )

    decision = parsed.decisions["image_01"]
    assert decision.auto_eligible is True
    assert decision.visible_identity == "Dyson"
    assert "silver/nickel body with metallic copper accents" in decision.visible_configuration
    assert decision.target_conflicts == ""


def test_ownership_parser_rejects_auto_eligible_decision_that_declares_conflict() -> None:
    facts = _facts(colour="rose-gold-toned body with copper accents")
    with pytest.raises(ListingImageRankingError, match="target_conflicts"):
        _parse_ownership(
            {
                "decisions": {
                    "image_01": {
                        "target_conflicts": "Frozen rose-gold finish conflicts with target variant.",
                        "target_match_evidence": "Same broad product family.",
                        "target_identity_gaps": "",
                        "classification": "EXACT_TARGET",
                        "confidence": 0.9,
                        "reason": "synthetic inconsistent output",
                    }
                },
                "summary": "inconsistent",
            },
            [_candidate()],
            facts,
        )


def test_gallery_has_no_target_identity_and_only_ranks_identity_approved_candidates() -> None:
    facts = _facts()
    ownership = _ParsedOwnership(
        decisions={
            "image_01": _parse_ownership(
                {
                    "decisions": {
                        "image_01": {
                            "target_conflicts": "",
                            "target_match_evidence": "Frozen facts establish exact target.",
                            "target_identity_gaps": "",
                            "classification": "EXACT_TARGET",
                            "confidence": 0.95,
                            "reason": "exact",
                        }
                    },
                    "summary": "exact",
                },
                [_candidate()],
                facts,
            ).decisions["image_01"]
        },
        summary="exact",
    )
    request = build_listing_image_ranking_request(
        product_context={"name": "must not be exposed"},
        candidates=[_candidate()],
        ownership=ownership,
        visual_facts=facts,
    )

    assert request["task"] == "order_identity_approved_supplier_gallery"
    assert request["context"]["decision_protocol"] == "identity_frozen_quality_ordering_v2"
    assert "target_product" not in request["context"]
    assert len(request["grounded_sources"]) == 1
    assert "not given target_product" in request["system_instruction"].lower()
    rules = "\n".join(request["rules"]).lower()
    assert "do not infer, verify or alter target identity" in rules
    assert "photo quality" in rules


def test_generic_colour_family_is_explicitly_insufficient_for_exact_variant_proof() -> None:
    request = build_listing_image_ownership_request(
        product_context={"variant": "Ceramic Pink"},
        candidates=[_candidate()],
        visual_facts=_facts(colour="pink body", uncertainty="exact seller colourway unresolved"),
    )
    rules = "\n".join(request["rules"]).lower()
    assert "generic visual terms are not exact seller-variant proof" in rules
    assert "broad colour family is insufficient" in request["prompt_instruction"].lower()
