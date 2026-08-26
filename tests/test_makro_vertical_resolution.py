from __future__ import annotations

import inspect

import pytest

import app.makro.vertical_resolution as vertical_resolution
import app.makro.vertical_selection as vertical_selection
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


def _ultrasonic_cleaner_hints() -> ListingBootstrapHints:
    summary = "Ultrasonic jewelry cleaner machine for cleaning rings, necklaces and glasses."
    return ListingBootstrapHints(
        vertical_search_terms=("ultrasonic jewelry cleaner",),
        brand="",
        brand_status="unknown",
        product_summary=summary,
        product_identity={
            "entity_kind": "physical_product",
            "product_type_en": "ultrasonic jewelry cleaner",
            "brand": "",
            "brand_status": "unknown",
            "product_summary": summary,
            "confidence": 0.95,
            "evidence_refs": ["identity:page-title"],
        },
    )


def test_search_plan_contract_has_specific_alternate_broader_and_discriminative_head_roles() -> None:
    request = build_vertical_search_plan_request(_bag_sealer_hints())
    properties = request["json_contract"]["properties"]

    assert set(properties) == {
        "specific_queries",
        "alternate_queries",
        "broader_queries",
        "head_noun_query",
    }
    assert properties["specific_queries"]["maxItems"] == 2
    assert properties["alternate_queries"]["maxItems"] == 2
    assert properties["broader_queries"]["maxItems"] == 2
    assert request["json_contract"]["required"] == [
        "specific_queries",
        "alternate_queries",
        "broader_queries",
        "head_noun_query",
    ]
    rules = " ".join(request["rules"]).casefold()
    assert "specific -> alternate vocabulary -> broader -> discriminative head phrase" in rules
    assert "do not collapse" in rules


def test_search_planner_preserves_specific_alternate_and_broader_then_appends_canonical() -> None:
    provider = FakeProvider(
        {
            "plan_makro_vertical_search_intents": {
                "specific_queries": ["bag sealer", "heat sealer"],
                "alternate_queries": ["bag sealing machine"],
                "broader_queries": ["sealing equipment"],
                "head_noun_query": "bag sealer",
            }
        }
    )

    terms = plan_vertical_search_terms(provider, _bag_sealer_hints())

    assert terms == (
        "bag sealer",
        "heat sealer",
        "bag sealing machine",
        "sealing equipment",
        "rechargeable bag sealer",
    )
    request = provider.requests[0]
    assert request["context"]["product_type_en"] == "rechargeable bag sealer"


def test_search_planner_deduplicates_head_and_keeps_canonical_fallback_last() -> None:
    provider = FakeProvider(
        {
            "plan_makro_vertical_search_intents": {
                "specific_queries": ["bag sealer", "heat sealer"],
                "alternate_queries": ["bag sealing machine"],
                "broader_queries": ["sealing equipment"],
                "head_noun_query": "bag sealer",
            }
        }
    )

    assert plan_vertical_search_terms(provider, _bag_sealer_hints()) == (
        "bag sealer",
        "heat sealer",
        "bag sealing machine",
        "sealing equipment",
        "rechargeable bag sealer",
    )


def test_search_planner_does_not_duplicate_canonical_when_ai_already_planned_it() -> None:
    provider = FakeProvider(
        {
            "plan_makro_vertical_search_intents": {
                "specific_queries": ["rechargeable bag sealer", "heat sealer"],
                "alternate_queries": ["bag sealing machine"],
                "broader_queries": ["bag sealer"],
                "head_noun_query": "heat sealer",
            }
        }
    )

    terms = plan_vertical_search_terms(provider, _bag_sealer_hints())

    assert terms == (
        "rechargeable bag sealer",
        "bag sealing machine",
        "bag sealer",
        "heat sealer",
    )
    assert terms.count("rechargeable bag sealer") == 1


def test_search_planner_fallback_keeps_discriminating_two_word_head() -> None:
    provider = FakeProvider(
        {"plan_makro_vertical_search_intents": RuntimeError("temporary provider failure")}
    )

    assert plan_vertical_search_terms(provider, _bag_sealer_hints()) == (
        "rechargeable bag sealer",
        "bag sealer",
    )


def test_invalid_generic_head_does_not_destroy_other_valid_planner_queries() -> None:
    provider = FakeProvider(
        {
            "plan_makro_vertical_search_intents": {
                "specific_queries": ["bag sealer"],
                "alternate_queries": [],
                "broader_queries": [],
                "head_noun_query": "machine",
            }
        }
    )

    assert plan_vertical_search_terms(provider, _bag_sealer_hints()) == (
        "bag sealer",
        "rechargeable bag sealer",
    )


def test_multiword_ultrasonic_cleaner_never_degrades_to_bare_cleaner() -> None:
    provider = FakeProvider(
        {
            "plan_makro_vertical_search_intents": {
                "specific_queries": ["ultrasonic jewelry cleaner"],
                "alternate_queries": [
                    "ultrasonic cleaning machine",
                    "jewelry cleaning machine",
                ],
                "broader_queries": ["ultrasonic cleaner"],
                "head_noun_query": "cleaner",
            }
        }
    )

    terms = plan_vertical_search_terms(provider, _ultrasonic_cleaner_hints())

    assert terms == (
        "ultrasonic jewelry cleaner",
        "ultrasonic cleaning machine",
        "jewelry cleaning machine",
        "ultrasonic cleaner",
    )
    assert "cleaner" not in terms
    assert vertical_resolution._usable_head_query_for_product(
        _ultrasonic_cleaner_hints(), "cleaner"
    ) is False


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
                "bag sealing machine",
                [
                    "Home Appliances / Kitchen Appliances / Bag Sealers",
                    "Home Improvement / Hardware & Electricals / Sealer",
                ],
            ),
        ]
    )

    assert candidates[0].label == "Home Appliances / Kitchen Appliances / Bag Sealers"
    assert candidates[0].matched_queries == ("bag sealer", "bag sealing machine")
    assert candidates[0].hit_count == 2
    assert candidates[0].best_rank == 1


def test_aggregated_chooser_can_only_return_one_exact_live_candidate() -> None:
    candidates = merge_vertical_search_observations(
        [
            ("bag sealer", ["Home / Kitchen / Bag Sealers", "Battery / Battery Chargers"]),
            ("bag sealing machine", ["Home / Kitchen / Bag Sealers"]),
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
        ("bag sealer", "bag sealing machine"),
        candidates,
    )

    assert selected == "Home / Kitchen / Bag Sealers"
    request = provider.requests[0]
    assert request["context"]["product_identity"]["product_type_en"] == "rechargeable bag sealer"
    assert request["context"]["search_queries_specific_to_broad"] == [
        "bag sealer",
        "bag sealing machine",
    ]
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


def test_pool_prompt_rejects_generic_word_overlap_as_category_evidence() -> None:
    candidates = merge_vertical_search_observations(
        [("ultrasonic cleaner", ["Household Care / Home Care / Toilet Cleaner"])]
    )
    request = build_vertical_pool_choice_request(
        _ultrasonic_cleaner_hints(),
        ("ultrasonic cleaner",),
        candidates,
    )
    rules = " ".join(request["rules"]).casefold()
    assert "precision for recall" in rules
    assert "must never add" in rules
    assert "generic word overlap" in rules
    assert "toilet cleaner" in rules


def test_production_search_collects_global_pool_then_rebinds_to_owner_query_before_click() -> None:
    source = inspect.getsource(vertical_selection._try_select_via_search)

    collect_pos = source.index("merge_vertical_search_observations(observations)")
    choose_pos = source.index("choose_vertical_candidate_pool")
    rebind_pos = source.index("for rebind_index, owner_query in enumerate(owner_queries")
    click_pos = source.index("click_search_row(search, rebound, allow_stable_exact=False)")

    assert collect_pos < choose_pos < rebind_pos < click_pos
    assert "matched_queries_for_candidate" in source
    assert "len(exact) != 1" in source
    assert "could not be re-observed uniquely" in source
