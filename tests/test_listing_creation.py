from __future__ import annotations

import pytest

from app.makro.listing_creation import (
    ListingBootstrapHints,
    _brand_search_terms,
    _parse_bootstrap_response,
    build_bootstrap_request,
    choose_brand_candidate,
    choose_vertical_candidate,
    normalize_label,
)
from app.providers.openai_compatible import _prompt_payload
from app.source_snapshot import SnapshotTableRow, SourceSnapshot


class FakeProvider:
    name = "fake"

    def __init__(self, response):
        self.response = response
        self.requests = []

    def extract_json(self, request_payload):
        self.requests.append(request_payload)
        return self.response


def _snapshot() -> SourceSnapshot:
    return SourceSnapshot(
        requested_url="https://supplier.example/item/1",
        final_url="https://supplier.example/item/1",
        title="M8 WiFi dual dash camera",
        captured_at="2026-08-09T00:00:00Z",
        visible_text="X" * 12000,
        table_rows=[SnapshotTableRow("Brand", "Neutral", 0, 0)],
        embedded_data=["Y" * 5000],
    )


def test_bootstrap_request_is_bounded_source_only_and_reaches_provider_payload():
    request = build_bootstrap_request(_snapshot())
    payload = _prompt_payload(request)
    evidence = payload["context"]["supplier_evidence"]
    assert len(evidence["visible_text"]) == 9000
    assert sum(len(item) for item in evidence["embedded_product_data"]) <= 3000
    assert evidence["table_rows"] == [{"key": "Brand", "value": "Neutral"}]
    assert request["task"] == "infer_product_listing_bootstrap_hints"
    assert payload["context"]["supplier_evidence"]["page_title"] == "M8 WiFi dual dash camera"


def test_parse_bootstrap_explicit_brand_and_dedup_terms():
    hints = _parse_bootstrap_response(
        {
            "vertical_search_terms": ["Dash Camera", "dash-camera", "Vehicle Camera"],
            "brand": "70mai",
            "brand_status": "explicit",
            "product_summary": "dash camera",
        }
    )
    assert hints.vertical_search_terms == ("Dash Camera", "Vehicle Camera")
    assert hints.brand == "70mai"
    assert hints.brand_status == "explicit"


def test_bootstrap_rejects_marketplace_or_listing_words_as_product_type_hints():
    with pytest.raises(ValueError, match="no usable product-type"):
        _parse_bootstrap_response(
            {
                "vertical_search_terms": ["Makro", "Marketplace Listing", "Vertical"],
                "brand": "",
                "brand_status": "unknown",
                "product_summary": "dash camera",
            }
        )


def test_unknown_brand_is_cleared_and_has_no_search_terms():
    hints = _parse_bootstrap_response(
        {
            "vertical_search_terms": ["Dash Camera"],
            "brand": "M8",
            "brand_status": "unknown",
            "product_summary": "dash camera",
        }
    )
    assert hints.brand == ""
    assert _brand_search_terms(hints) == ()


def test_unbranded_uses_only_platform_sentinel_searches():
    hints = ListingBootstrapHints(("Dash Camera",), "", "unbranded", "dash camera")
    assert _brand_search_terms(hints) == ("Unbranded", "No Brand", "Generic")


def test_vertical_choice_must_be_one_live_candidate_and_receives_context():
    hints = ListingBootstrapHints(("Vehicle Camera",), "", "unbranded", "dash camera")
    provider = FakeProvider({"selected_vertical": "Vehicle Camera System"})
    selected = choose_vertical_candidate(
        provider,
        hints,
        "Vehicle Camera",
        ["Vehicle Camera System", "Automotive Spares"],
    )
    assert selected == "Vehicle Camera System"
    request = provider.requests[0]
    assert request["context"] == {
        "product_summary": "dash camera",
        "search_term": "Vehicle Camera",
        "live_candidates": ["Vehicle Camera System", "Automotive Spares"],
    }
    assert _prompt_payload(request)["context"] == request["context"]


def test_brand_choice_receives_supplier_status_and_live_candidates():
    hints = ListingBootstrapHints(("Camera",), "", "unbranded", "camera")
    provider = FakeProvider({"selected_brand": "Generic"})
    selected = choose_brand_candidate(provider, hints, "Generic", ["Generic", "Samsung"])
    assert selected == "Generic"
    request = provider.requests[0]
    assert request["context"] == {
        "brand_status": "unbranded",
        "supplier_brand": "",
        "search_term": "Generic",
        "live_candidates": ["Generic", "Samsung"],
    }
    assert _prompt_payload(request)["context"] == request["context"]


def test_brand_explicit_exact_match_does_not_need_ai():
    hints = ListingBootstrapHints(("Camera",), "70mai", "explicit", "camera")
    provider = FakeProvider({"selected_brand": "wrong"})
    selected = choose_brand_candidate(provider, hints, "70mai", ["70MAI", "Generic"])
    assert selected == "70MAI"
    assert provider.requests == []


def test_normalize_label_ignores_punctuation_and_case():
    assert normalize_label("Vehicle-Camera System") == normalize_label("vehicle camera system")
