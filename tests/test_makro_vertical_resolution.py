from __future__ import annotations

import pytest

import app.makro.vertical_resolution as vertical_resolution
from app.makro.listing_creation import ListingBootstrapHints
from app.makro.vertical_resolution import (
    build_vertical_pool_choice_request,
    build_vertical_search_plan_request,
    choose_vertical_candidate_pool,
    merge_vertical_search_observations,
    plan_vertical_search_terms,
)


class FakeProvider:
    name = "fake"

    def __init__(self, responses):
        self.responses = dict(responses)
        self.requests = []

    def extract_json(self, request_payload):
        self.requests.append(request_payload)
        task = request_payload["task"]
        response = self.responses[task]
        if isinstance(response, Exception):
            raise response
        return response


def _bag_sealer_hints() -> ListingBootstrapHints:
    summary = "Rechargeable handheld heat sealer for closing plastic food bags."
    return ListingBootstrapHints(
        vertical_search_terms=("rechargeable bag sealer",),
        brand="",
        brand_status="unknown",
        product_summary=summary,
        product_identity={
            "entity_kind": "physical_product",
            "product_type_en": "rechargeable bag sealer",
            "brand": "",
            "brand_status": "unknown",
            "product_summary": summary,
            "confidence": 0.95,
            "evidence_refs": ["identity:page-title"],
        },
    )


def test_search_plan_contract_has_specific_broader_and_head_noun_roles() -> None:
    request = build_vertical_search_plan_request(_bag_sealer_hints())
    properties = request["json_contract"]["properties"]

    assert set(properties) == {
        "specific_queries",
        "broader_queries",
        "head_noun_query",
    }
    assert properties["specific_queries"]["maxItems"] == 2
    assert properties["broader_queries"]["maxItems"] == 2
    assert request["json_contract"]["required"] == [
        "specific_queries",
        "broader_queries",
        "head_noun_query",
    ]
    rules = " ".join(request["rules"]).casefold()
    assert "specific -> broader -> head noun" in rules


def test_search_planner_preserves_ai_ladder_then_appends_missing_canonical_product_type() -> None:
    provider = FakeProvider(
        {
            "plan_makro_vertical_search_intents": {
                "specific_queries": ["bag sealer", "heat sealer"],
                "broader_queries": ["bag sealer"],
                "head_noun_query": "sealer",
            }
        }
    )

    terms = plan_vertical_search_terms(provider, _bag_sealer_hints())

    assert terms == (
        "bag sealer",
        "heat sealer",
        "sealer",
        "rechargeable bag sealer",
    )
    assert terms[:-1] == ("bag sealer", "heat sealer", "sealer")
    assert terms[-1] == "rechargeable bag sealer"
    request = provider.requests[0]
    assert request["context"]["product_type_en"] == "rechargeable bag sealer"


def test_search_planner_deduplicates_head_and_keeps_canonical_fallback_last() -> None:
    provider = FakeProvider(
        {
            "plan_makro_vertical_search_intents": {
                "specific_queries": ["sealer", "heat sealer"],
                "broader_queries": ["bag sealer"],
                "head_noun_query": "sealer",
            }
        }
    )

    assert plan_vertical_search_terms(provider, _bag_sealer_hints()) == (
        "heat sealer",
        "bag sealer",
        "sealer",
        "rechargeable bag sealer",
    )


def test_search_planner_does_not_duplicate_canonical_when_ai_already_planned_it() -> None:
    provider = FakeProvider(
        {
            "plan_makro_vertical_search_intents": {
                "specific_queries": ["rechargeable bag sealer", "heat sealer"],
                "broader_queries": ["bag sealer"],
                "head_noun_query": "sealer",
            }
        }
    )

    terms = plan_vertical_search_terms(provider, _bag_sealer_hints())

    assert terms == (
        "rechargeable bag sealer",
        "heat sealer",
        "bag sealer",
        "sealer",
    )
    assert terms.count("rechargeable bag sealer") == 1


def test_search_planner_fallback_also_broadens_to_head_noun() -> None:
    provider = FakeProvider(
        {"plan_makro_vertical_search_intents": RuntimeError("temporary provider failure")}
    )

    assert plan_vertical_search_terms(provider, _bag_sealer_hints()) == (
        "rechargeable bag sealer",
        "bag sealer",
        "sealer",
    )


def test_invalid_planner_head_falls_back_instead_of_searching_generic_machine() -> None:
    provider = FakeProvider(
        {
            "plan_makro_vertical_search_intents": {
                "specific_queries": ["bag sealer"],
                "broader_queries": [],
                "head_noun_query": "machine",
            }
        }
    )

    assert plan_vertical_search_terms(provider, _bag_sealer_hints()) == (
        "rechargeable bag sealer",
        "bag sealer",
        "sealer",
    )


def test_search_query_guard_rejects_platform_pollution_without_blocking_real_product_names() -> None:
    assert vertical_resolution._usable_query("Makro bag sealer") is False
    assert vertical_resolution._usable_query("seller category") is False
    assert vertical_resolution._usable_query("vertical") is False
    assert vertical_resolution._usable_query("category") is False
    assert vertical_resolution._usable_query("vertical blinds") is True
    assert vertical_resolution._usable_query("category 6 cable") is True


def test_live_candidates_are_aggregated_across_queries_before_selection() -> None:
    candidates = merge_vertical_search_observations(
        [
            (
                "bag sealer",
                [
                    "Home Appliances / Kitchen Appliances / Bag Sealers",
                    "Sports & Fitness / Accessories / Pellets Recharge",
                ],
            ),
            (
                "sealer",
                [
                    "Home Appliances / Kitchen Appliances / Bag Sealers",
                    "Home Improvement / Hardware & Electricals / Sealer",
                ],
            ),
        ]
    )

    assert candidates[0].label == "Home Appliances / Kitchen Appliances / Bag Sealers"
    assert candidates[0].matched_queries == ("bag sealer", "sealer")
    assert candidates[0].hit_count == 2
    assert candidates[0].best_rank == 1


def test_aggregated_chooser_can_only_return_one_exact_live_candidate() -> None:
    candidates = merge_vertical_search_observations(
        [
            ("bag sealer", ["Home / Kitchen / Bag Sealers", "Battery / Battery Chargers"]),
            ("sealer", ["Home / Kitchen / Bag Sealers"]),
        ]
    )
    provider = FakeProvider(
        {
            "choose_exact_makro_vertical_from_aggregated_live_search": {
                "selected_vertical": "Home / Kitchen / Bag Sealers",
                "selection_relation": "same_product_type",
            }
        }
    )

    selected = choose_vertical_candidate_pool(
        provider,
        _bag_sealer_hints(),
        ("bag sealer", "sealer"),
        candidates,
    )

    assert selected == "Home / Kitchen / Bag Sealers"
    request = provider.requests[0]
    assert request["context"]["product_identity"]["product_type_en"] == "rechargeable bag sealer"
    assert request["context"]["search_queries_specific_to_broad"] == ["bag sealer", "sealer"]
    assert request["json_contract"]["properties"]["selected_vertical"]["enum"] == [
        "",
        "Home / Kitchen / Bag Sealers",
        "Battery / Battery Chargers",
    ]


def test_aggregated_chooser_rejects_invented_vertical() -> None:
    candidates = merge_vertical_search_observations(
        [("bag sealer", ["Home / Kitchen / Bag Sealers"])]
    )
    provider = FakeProvider(
        {
            "choose_exact_makro_vertical_from_aggregated_live_search": {
                "selected_vertical": "Invented / Heat Sealing Machine",
                "selection_relation": "same_product_type",
            }
        }
    )

    with pytest.raises(ValueError, match="not one unique aggregated live candidate"):
        choose_vertical_candidate_pool(
            provider,
            _bag_sealer_hints(),
            ("bag sealer",),
            candidates,
        )


def test_pool_prompt_knows_broad_queries_trade_precision_for_recall() -> None:
    candidates = merge_vertical_search_observations(
        [("sealer", ["Home Improvement / Hardware & Electricals / Sealer"])]
    )
    request = build_vertical_pool_choice_request(
        _bag_sealer_hints(),
        ("bag sealer", "sealer"),
        candidates,
    )
    rules = " ".join(request["rules"]).casefold()
    assert "precision for recall" in rules
    assert "must never add" in rules
