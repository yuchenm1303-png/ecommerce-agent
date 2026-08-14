from __future__ import annotations

import pytest

import app.makro.vertical_selection as vertical_selection
from app.makro.listing_creation import ListingBootstrapHints
from app.makro.vertical_resolution import (
    build_vertical_pool_choice_request,
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
    return ListingBootstrapHints(
        vertical_search_terms=("rechargeable bag sealer",),
        brand="",
        brand_status="unknown",
        product_summary="Rechargeable handheld heat sealer for closing plastic food bags.",
        product_identity={
            "entity_kind": "physical_product",
            "product_type_en": "rechargeable bag sealer",
            "brand": "",
            "brand_status": "unknown",
            "product_summary": "Rechargeable handheld heat sealer for closing plastic food bags.",
            "confidence": 0.95,
            "evidence_refs": ["identity:page-title"],
        },
    )


def test_search_planner_replaces_raw_identity_phrase_with_clean_retrieval_intents() -> None:
    provider = FakeProvider(
        {
            "plan_makro_vertical_search_intents": {
                "queries": ["bag sealer", "heat sealer", "sealing machine"]
            }
        }
    )

    terms = plan_vertical_search_terms(provider, _bag_sealer_hints())

    assert terms == ("bag sealer", "heat sealer", "sealing machine")
    assert "rechargeable bag sealer" not in terms
    request = provider.requests[0]
    assert request["context"]["product_type_en"] == "rechargeable bag sealer"
    assert "power source" in " ".join(request["rules"]).casefold()


def test_search_planner_falls_back_to_grounded_product_type_if_planner_fails() -> None:
    provider = FakeProvider(
        {"plan_makro_vertical_search_intents": RuntimeError("temporary provider failure")}
    )

    assert plan_vertical_search_terms(provider, _bag_sealer_hints()) == (
        "rechargeable bag sealer",
    )


def test_search_planner_falls_back_when_response_contains_no_safe_queries() -> None:
    provider = FakeProvider(
        {
            "plan_makro_vertical_search_intents": {
                "queries": ["Makro vertical", "seller category", ""]
            }
        }
    )

    assert plan_vertical_search_terms(provider, _bag_sealer_hints()) == (
        "rechargeable bag sealer",
    )


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
                "heat sealer",
                [
                    "Home Appliances / Kitchen Appliances / Bag Sealers",
                    "Automotive Spares / Spare Parts / Turbocharger",
                ],
            ),
        ]
    )

    assert candidates[0].label == "Home Appliances / Kitchen Appliances / Bag Sealers"
    assert candidates[0].matched_queries == ("bag sealer", "heat sealer")
    assert candidates[0].hit_count == 2
    assert candidates[0].best_rank == 1
    assert {item.label for item in candidates[1:]} == {
        "Sports & Fitness / Accessories / Pellets Recharge",
        "Automotive Spares / Spare Parts / Turbocharger",
    }


def test_aggregated_chooser_can_only_return_one_exact_live_candidate() -> None:
    candidates = merge_vertical_search_observations(
        [
            ("bag sealer", ["Home / Kitchen / Bag Sealers", "Battery / Battery Chargers"]),
            ("heat sealer", ["Home / Kitchen / Bag Sealers"]),
        ]
    )
    provider = FakeProvider(
        {
            "choose_exact_makro_vertical_from_aggregated_live_search": {
                "selected_vertical": "Home / Kitchen / Bag Sealers"
            }
        }
    )

    selected = choose_vertical_candidate_pool(
        provider,
        _bag_sealer_hints(),
        ("bag sealer", "heat sealer"),
        candidates,
    )

    assert selected == "Home / Kitchen / Bag Sealers"
    request = provider.requests[0]
    assert request["context"]["product_identity"]["product_type_en"] == "rechargeable bag sealer"
    good = request["context"]["live_candidates"][0]
    assert good["matched_queries"] == ["bag sealer", "heat sealer"]
    assert good["query_hit_count"] == 2
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
                "selected_vertical": "Invented / Heat Sealing Machine"
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


def test_pool_prompt_treats_search_queries_as_retrieval_hints_not_truth() -> None:
    candidates = merge_vertical_search_observations(
        [("rechargeable bag sealer", ["Battery / Battery Chargers"])]
    )
    request = build_vertical_pool_choice_request(
        _bag_sealer_hints(),
        ("rechargeable bag sealer",),
        candidates,
    )
    rules = " ".join(request["rules"]).casefold()
    assert "retrieval hint" in rules
    assert "power term" in rules
    assert "chargers" in rules


class FakeSearch:
    def __init__(self) -> None:
        self.current = ""
        self.nonempty_fills: list[str] = []

    def fill(self, value: str) -> None:
        self.current = value
        if value:
            self.nonempty_fills.append(value)

    def press(self, _key: str) -> None:
        return None


class FakePage:
    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


def test_live_search_collects_every_query_then_replays_selected_query_before_click(monkeypatch) -> None:
    page = FakePage()
    search = FakeSearch()
    rows_by_query = {
        "bag sealer": [
            "Home / Kitchen / Bag Sealers",
            "Sports / Accessories / Pellets Recharge",
        ],
        "heat sealer": ["Home / Kitchen / Bag Sealers"],
        "sealing machine": ["Industrial / Packaging / Sealing Machines"],
    }
    clicked: list[tuple[str, str]] = []

    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(vertical_selection, "begin_search_query", lambda _search: None)
    monkeypatch.setattr(
        vertical_selection,
        "plan_vertical_search_terms",
        lambda _provider, _hints: ("bag sealer", "heat sealer", "sealing machine"),
    )
    monkeypatch.setattr(
        vertical_selection,
        "_wait_for_scoped_vertical_search_candidates",
        lambda _page, active, **_kwargs: list(rows_by_query.get(active.current, [])),
    )
    monkeypatch.setattr(
        vertical_selection,
        "choose_vertical_candidate_pool",
        lambda _provider, _hints, _terms, _pool: "Home / Kitchen / Bag Sealers",
    )
    monkeypatch.setattr(vertical_selection, "_current_target_values", lambda _page: ("", ""))
    monkeypatch.setattr(
        vertical_selection,
        "click_search_row",
        lambda active, label: clicked.append((active.current, label)) or True,
    )
    monkeypatch.setattr(
        vertical_selection,
        "_complete_exact_live_vertical",
        lambda _page, _selected, **_kwargs: "bag_sealer",
    )

    selected, observed, planned = vertical_selection._try_select_via_search(
        page,
        object(),
        _bag_sealer_hints(),
        wait_ms=0,
    )

    assert planned == ("bag sealer", "heat sealer", "sealing machine")
    assert search.nonempty_fills == [
        "bag sealer",
        "heat sealer",
        "sealing machine",
        "bag sealer",
    ]
    assert clicked == [("bag sealer", "Home / Kitchen / Bag Sealers")]
    assert selected == "bag_sealer"
    assert "Sports / Accessories / Pellets Recharge" in observed
