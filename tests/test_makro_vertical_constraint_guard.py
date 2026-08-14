from __future__ import annotations

from app.makro.listing_creation import ListingBootstrapHints
from app.makro.vertical_resolution import (
    choose_vertical_candidate_pool,
    merge_vertical_search_observations,
    unsupported_candidate_constraints,
)


class FakeProvider:
    name = "fake"

    def __init__(self, response):
        self.response = response

    def extract_json(self, _request_payload):
        return dict(self.response)


def _bag_sealer_hints() -> ListingBootstrapHints:
    summary = "Rechargeable handheld heat sealer for closing plastic food bags."
    return ListingBootstrapHints(
        vertical_search_terms=("rechargeable bag sealer",),
        brand="VortecSys",
        brand_status="explicit",
        product_summary=summary,
        product_identity={
            "entity_kind": "physical_product",
            "product_type_en": "rechargeable bag sealer",
            "brand": "VortecSys",
            "brand_status": "explicit",
            "product_summary": summary,
            "confidence": 0.95,
            "evidence_refs": ["identity:page-title"],
        },
    )


def test_category_constraint_guard_allows_broader_or_grounded_leafs() -> None:
    hints = _bag_sealer_hints()
    assert unsupported_candidate_constraints(hints, "Home / Kitchen / Sealer") == ()
    assert unsupported_candidate_constraints(hints, "Home / Kitchen / Bag Sealers") == ()
    assert unsupported_candidate_constraints(hints, "Home / Kitchen / Heat Sealers") == ()
    assert unsupported_candidate_constraints(hints, "Industrial / Packaging / Sealing Machines") == ()


def test_category_constraint_guard_rejects_unsupported_specific_capability() -> None:
    hints = _bag_sealer_hints()
    assert unsupported_candidate_constraints(
        hints,
        "Home & Kitchen Accessories / Kitchen Tools / Vacuum Bag Sealer",
    ) == ("vacuum",)


def test_ai_cannot_force_vacuum_sibling_even_if_it_calls_it_same_product_type() -> None:
    hints = _bag_sealer_hints()
    label = "Home & Kitchen Accessories / Kitchen Tools / Vacuum Bag Sealer"
    candidates = merge_vertical_search_observations([("bag sealer", [label])])
    provider = FakeProvider(
        {"selected_vertical": label, "selection_relation": "same_product_type"}
    )
    assert choose_vertical_candidate_pool(
        provider,
        hints,
        ("bag sealer",),
        candidates,
    ) == ""


def test_valid_broader_class_remains_selectable() -> None:
    hints = _bag_sealer_hints()
    label = "Home Improvement / Hardware & Electricals / Sealer"
    candidates = merge_vertical_search_observations([("bag sealer", [label])])
    provider = FakeProvider(
        {"selected_vertical": label, "selection_relation": "broader_valid_class"}
    )
    assert choose_vertical_candidate_pool(
        provider,
        hints,
        ("bag sealer",),
        candidates,
    ) == label
