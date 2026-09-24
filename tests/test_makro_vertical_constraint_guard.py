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


def test_category_constraint_analysis_still_describes_grounded_leafs() -> None:
    hints = _bag_sealer_hints()
    assert unsupported_candidate_constraints(hints, "Home / Kitchen / Sealer") == ()
    assert unsupported_candidate_constraints(hints, "Home / Kitchen / Bag Sealers") == ()
    assert unsupported_candidate_constraints(hints, "Home / Kitchen / Heat Sealers") == ()
    assert unsupported_candidate_constraints(hints, "Industrial / Packaging / Sealing Machines") == ()


def test_category_constraint_analysis_reports_mismatch_for_diagnostics() -> None:
    hints = _bag_sealer_hints()
    assert unsupported_candidate_constraints(
        hints,
        "Home & Kitchen Accessories / Kitchen Tools / Vacuum Bag Sealer",
    ) == ("vacuum",)


def test_constraint_analysis_no_longer_vetoes_ai_best_available_live_choice() -> None:
    hints = _bag_sealer_hints()
    label = "Home & Kitchen Accessories / Kitchen Tools / Vacuum Bag Sealer"
    candidates = merge_vertical_search_observations([("bag sealer", [label])])
    provider = FakeProvider(
        {"selected_vertical": label, "selection_relation": "best_available_fit"}
    )
    assert choose_vertical_candidate_pool(
        provider,
        hints,
        ("bag sealer",),
        candidates,
    ) == label


def test_ai_selected_real_live_candidate_is_not_silently_overridden_by_lexical_guard() -> None:
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
    ) == label


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
