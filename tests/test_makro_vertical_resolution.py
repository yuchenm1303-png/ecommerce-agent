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


def _protective_glasses_hints() -> ListingBootstrapHints:
    """Regression fixture: the first AI interpretation is deliberately over-specific."""

    return ListingBootstrapHints(
        vertical_search_terms=("personalized wooden sunglasses",),
        brand="",
        brand_status="unknown",
        product_summary="Polarized eyewear with a wooden-style frame.",
        product_identity={
            "entity_kind": "physical_product",
            "product_type_en": "personalized wooden sunglasses",
            "brand": "",
            "brand_status": "unknown",
            "product_summary": "Personalized wooden polarized sunglasses.",
            "confidence": 0.78,
            "evidence_refs": ["identity:page-title"],
        },
        customer_intent="1个偏光防护镜",
        grounded_product_evidence=(
            "supplier_product_heading: Polarized protective eyewear with impact-resistant lenses and wooden-style frame",
        ),
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
    assert "core product class -> alternate retail vocabulary -> broader family -> discriminative head phrase" in rules
    assert "do not collapse" in rules
    assert "initial_product_identity as a hypothesis" in rules


def test_search_planner_preserves_role_order_then_appends_canonical() -> None:
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
        "heat sealer",
        "bag sealing machine",
        "sealing equipment",
        "bag sealer",
        "rechargeable bag sealer",
    )
    request = provider.requests[0]
    assert request["context"]["product_type_en"] == "rechargeable bag sealer"
    assert request["context"]["initial_product_identity"]["product_type_en"] == "rechargeable bag sealer"
    assert request["context"]["grounded_supplier_evidence"] == []
    assert request["context"]["customer_listing_intent"] == ""


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
        "heat sealer",
        "bag sealing machine",
        "sealing equipment",
        "bag sealer",
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


def test_live_candidate_evidence_builder_deduplicates_rows() -> None:
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


def test_ai_chooser_can_only_return_one_exact_live_candidate() -> None:
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
    assert request["context"]["initial_product_identity"]["product_type_en"] == "rechargeable bag sealer"
    assert request["context"]["grounded_supplier_evidence"] == []
    assert request["context"]["customer_listing_intent"] == ""
    assert request["context"]["search_queries_specific_to_broad"] == [
        "bag sealer",
        "bag sealing machine",
    ]
    assert request["json_contract"]["properties"]["selected_vertical"]["enum"] == [
        "",
        "Home / Kitchen / Bag Sealers",
        "Battery / Battery Chargers",
    ]


def test_automatic_exact_product_type_candidate_still_goes_through_ai() -> None:
    exact = "Home / Kitchen / Rechargeable Bag Sealer"
    hints = _bag_sealer_hints()
    candidates = merge_vertical_search_observations(
        [("rechargeable bag sealer", [exact])]
    )
    provider = FakeProvider(
        {
            "choose_exact_makro_vertical_from_aggregated_live_search": {
                "selected_vertical": exact,
                "selection_relation": "same_product_type",
            }
        }
    )

    assert choose_vertical_candidate_pool(
        provider,
        hints,
        ("rechargeable bag sealer",),
        candidates,
    ) == exact
    assert len(provider.requests) == 1


def test_initial_identity_can_be_corrected_by_independent_evidence_and_live_pool() -> None:
    hints = _protective_glasses_hints()
    candidates = merge_vertical_search_observations(
        [
            (
                "sunglasses",
                [
                    "Fashion / Eyewear / Personalized Wooden Sunglasses",
                    "Industrial & Scientific Supplies / Safety Products / Protective Glasses",
                ],
            )
        ]
    )
    provider = FakeProvider(
        {
            "choose_exact_makro_vertical_from_aggregated_live_search": {
                "selected_vertical": "Industrial & Scientific Supplies / Safety Products / Protective Glasses",
                "selection_relation": "best_available_fit",
            }
        }
    )

    selected = choose_vertical_candidate_pool(
        provider,
        hints,
        ("sunglasses", "protective glasses"),
        candidates,
    )

    assert selected == "Industrial & Scientific Supplies / Safety Products / Protective Glasses"
    assert len(provider.requests) == 1
    context = provider.requests[0]["context"]
    assert context["initial_product_identity"]["product_type_en"] == "personalized wooden sunglasses"
    assert context["customer_listing_intent"] == "1个偏光防护镜"
    assert "Polarized protective eyewear" in context["grounded_supplier_evidence"][0]
    allowed = provider.requests[0]["json_contract"]["properties"]["selected_vertical"]["enum"]
    assert selected in allowed


def test_reconciliation_prompt_never_turns_customer_intent_into_unbounded_authority() -> None:
    hints = _protective_glasses_hints()
    candidates = merge_vertical_search_observations(
        [("protective glasses", ["Industrial / Safety / Protective Glasses"])]
    )
    request = build_vertical_pool_choice_request(hints, ("protective glasses",), candidates)
    rules = " ".join(request["rules"]).casefold()

    assert "initial product identity is an interpretation" in rules
    assert "customer listing intent is independent context" in rules
    assert "never let it override supplier evidence" in rules
    assert "incidental attributes such as material, engraving, personalization" in rules


def test_ai_chooser_rejects_invented_vertical() -> None:
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

    with pytest.raises(ValueError, match="not one unique current live candidate"):
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
    assert "generic word overlap" in rules
    assert "complete breadcrumb" in rules
    assert "live candidate set is authoritative" in request["system_instruction"].casefold()
    assert "initial ai identity" in request["system_instruction"].casefold()
    assert "return none" in request["prompt_instruction"].casefold()


def test_production_search_decides_and_clicks_inside_same_live_generation() -> None:
    source = inspect.getsource(vertical_selection._try_select_via_search)

    query_pos = source.index("_run_vertical_search_query(page, term")
    pool_pos = source.index("merge_vertical_search_observations(((term, rows),))")
    choose_pos = source.index("choose_vertical_candidate_pool(")
    click_pos = source.index("click_search_row(search, current_row, allow_stable_exact=False)")

    assert query_pos < pool_pos < choose_pos < click_pos
    assert "matched_queries_for_candidate" not in source
    assert "prior_owner_queries" not in source
    assert "selected_row_rebind" not in source
    assert "rebind" not in source.casefold()
