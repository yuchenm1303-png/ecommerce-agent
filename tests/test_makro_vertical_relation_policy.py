from __future__ import annotations

import pytest

from app.makro.listing_creation import ListingBootstrapHints
from app.makro.vertical_resolution import (
    build_vertical_pool_choice_request,
    choose_vertical_candidate_pool,
    merge_vertical_search_observations,
)


class FakeProvider:
    name = "fake"

    def __init__(self, response):
        self.response = response

    def extract_json(self, _request_payload):
        return self.response


def _hints() -> ListingBootstrapHints:
    return ListingBootstrapHints(
        vertical_search_terms=("handheld heat sealer",),
        brand="",
        brand_status="unknown",
        product_summary="Handheld heat sealer for sealing plastic bags.",
        product_identity={
            "entity_kind": "physical_product",
            "product_type_en": "handheld heat sealer",
            "product_summary": "Handheld heat sealer for sealing plastic bags.",
        },
    )


def _candidates():
    return merge_vertical_search_observations(
        [
            (
                "heat sealer",
                [
                    "Home Improvement / Hardware & Electricals / Sealer",
                    "Home & Kitchen Accessories / Kitchen Tools / Vacuum Bag Sealer",
                ],
            )
        ]
    )


def test_pool_contract_allows_practical_best_available_fit() -> None:
    request = build_vertical_pool_choice_request(
        _hints(),
        ("heat sealer",),
        _candidates(),
    )
    schema = request["json_contract"]
    assert schema["required"] == ["selected_vertical", "selection_relation"]
    assert schema["properties"]["selection_relation"]["enum"] == [
        "same_product_type",
        "broader_valid_class",
        "best_available_fit",
        "none",
    ]
    rules = " ".join(request["rules"]).casefold()
    assert "best_available_fit" in rules
    assert "closest practical" in rules or "closest reasonable" in rules
    assert "same_product_type -> broader_valid_class -> best_available_fit -> none" in rules


def test_explicit_broader_valid_class_can_be_selected_from_exact_live_pool() -> None:
    selected = choose_vertical_candidate_pool(
        FakeProvider(
            {
                "selected_vertical": "Home Improvement / Hardware & Electricals / Sealer",
                "selection_relation": "broader_valid_class",
            }
        ),
        _hints(),
        ("heat sealer",),
        _candidates(),
    )
    assert selected == "Home Improvement / Hardware & Electricals / Sealer"


def test_best_available_fit_can_use_imperfect_real_live_category() -> None:
    label = "Home & Kitchen Accessories / Kitchen Tools / Vacuum Bag Sealer"
    selected = choose_vertical_candidate_pool(
        FakeProvider(
            {
                "selected_vertical": label,
                "selection_relation": "best_available_fit",
            }
        ),
        _hints(),
        ("heat sealer",),
        _candidates(),
    )
    assert selected == label


def test_none_relation_cannot_authorize_nonempty_candidate() -> None:
    with pytest.raises(ValueError, match="non-empty Makro Vertical selection"):
        choose_vertical_candidate_pool(
            FakeProvider(
                {
                    "selected_vertical": "Home & Kitchen Accessories / Kitchen Tools / Vacuum Bag Sealer",
                    "selection_relation": "none",
                }
            ),
            _hints(),
            ("heat sealer",),
            _candidates(),
        )


def test_empty_selection_must_be_relation_none() -> None:
    with pytest.raises(ValueError, match="empty Makro Vertical selection"):
        choose_vertical_candidate_pool(
            FakeProvider(
                {
                    "selected_vertical": "",
                    "selection_relation": "best_available_fit",
                }
            ),
            _hints(),
            ("heat sealer",),
            _candidates(),
        )
