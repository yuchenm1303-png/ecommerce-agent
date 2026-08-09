from __future__ import annotations

import pytest

import app.makro.listing_creation as listing_creation
from app.makro.listing_creation import (
    ListingBootstrapHints,
    _brand_search_terms,
    _parse_bootstrap_response,
    build_bootstrap_request,
    choose_brand_candidate,
    choose_vertical_candidate,
    normalize_label,
    select_brand,
    select_vertical,
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


class FakeInput:
    def __init__(self) -> None:
        self.values = []

    def fill(self, value):
        self.values.append(value)


class FakeButton:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.clicks = 0

    def click(self, **_kwargs):
        self.clicks += 1
        self.callback()


class StatePage:
    def __init__(self, phase="vertical") -> None:
        self.phase = phase
        self.url = "https://seller.makro.co.za/index.html#dashboard/addListings/single"

    def wait_for_timeout(self, _milliseconds):
        return None


def _vertical_test_setup(monkeypatch, page, *, body, confirmation_button):
    monkeypatch.setattr(listing_creation, "is_vertical_step", lambda current: current.phase == "vertical")
    monkeypatch.setattr(listing_creation, "is_brand_step", lambda current: current.phase == "brand")
    monkeypatch.setattr(listing_creation, "_vertical_search_input", lambda _page: FakeInput())
    monkeypatch.setattr(
        listing_creation,
        "_visible_text_candidates",
        lambda _page: ["Vehicle Camera System", "Automotive Spares"],
    )
    monkeypatch.setattr(
        listing_creation,
        "choose_vertical_candidate",
        lambda *_args, **_kwargs: "Vehicle Camera System",
    )
    monkeypatch.setattr(listing_creation, "_body_text", lambda _page: body())
    monkeypatch.setattr(
        listing_creation,
        "_vertical_select_brand_button",
        lambda _page: confirmation_button,
    )
    monkeypatch.setattr(
        listing_creation,
        "_current_target_values",
        lambda current: ("Vehicle Camera System", "") if current.phase == "brand" else ("", ""),
    )


def test_vertical_click_can_enter_brand_step_directly(monkeypatch):
    page = StatePage()
    _vertical_test_setup(monkeypatch, page, body=lambda: "", confirmation_button=None)
    monkeypatch.setattr(
        listing_creation,
        "_click_exact_visible_text",
        lambda current, _selected: setattr(current, "phase", "brand") or True,
    )

    selected = select_vertical(
        page,
        FakeProvider({}),
        ListingBootstrapHints(("dash camera",), "", "unbranded", "dash camera"),
        wait_ms=0,
    )

    assert selected == "Vehicle Camera System"
    assert page.phase == "brand"


def test_vertical_confirmation_clicks_exact_select_brand_before_step2(monkeypatch):
    page = StatePage()
    button = FakeButton(lambda: setattr(page, "phase", "brand"))
    _vertical_test_setup(
        monkeypatch,
        page,
        body=lambda: (
            "VERTICAL Vehicle Camera System\n"
            "Please select a brand to start selling in this vertical.\nSelect Brand"
        ),
        confirmation_button=button,
    )
    monkeypatch.setattr(
        listing_creation,
        "_click_exact_visible_text",
        lambda current, _selected: setattr(current, "phase", "vertical_confirmation") or True,
    )

    selected = select_vertical(
        page,
        FakeProvider({}),
        ListingBootstrapHints(("dash camera",), "", "unbranded", "dash camera"),
        wait_ms=0,
    )

    assert selected == "Vehicle Camera System"
    assert button.clicks == 1
    assert page.phase == "brand"


def test_vertical_confirmation_rejects_mismatched_selected_vertical(monkeypatch):
    page = StatePage()
    button = FakeButton(lambda: None)
    _vertical_test_setup(
        monkeypatch,
        page,
        body=lambda: (
            "VERTICAL Automotive Spares\n"
            "Please select a brand to start selling in this vertical.\nSelect Brand"
        ),
        confirmation_button=button,
    )
    monkeypatch.setattr(
        listing_creation,
        "_click_exact_visible_text",
        lambda current, _selected: setattr(current, "phase", "vertical_confirmation") or True,
    )

    with pytest.raises(RuntimeError, match="vertical confirmation mismatch"):
        select_vertical(
            page,
            FakeProvider({}),
            ListingBootstrapHints(("dash camera",), "", "unbranded", "dash camera"),
            wait_ms=0,
        )
    assert button.clicks == 0


def test_vertical_confirmation_without_select_brand_button_fails_clearly(monkeypatch):
    page = StatePage()
    _vertical_test_setup(
        monkeypatch,
        page,
        body=lambda: (
            "VERTICAL Vehicle Camera System\n"
            "Please select a brand to start selling in this vertical."
        ),
        confirmation_button=None,
    )
    monkeypatch.setattr(
        listing_creation,
        "_click_exact_visible_text",
        lambda current, _selected: setattr(current, "phase", "vertical_confirmation") or True,
    )

    with pytest.raises(RuntimeError, match="exact Select Brand button was not found"):
        select_vertical(
            page,
            FakeProvider({}),
            ListingBootstrapHints(("dash camera",), "", "unbranded", "dash camera"),
            wait_ms=0,
        )


def _brand_test_setup(monkeypatch, page, *, body, confirmation_button):
    monkeypatch.setattr(listing_creation, "is_brand_step", lambda current: current.phase == "brand")
    monkeypatch.setattr(listing_creation, "is_product_info_step", lambda current: current.phase == "product")
    monkeypatch.setattr(listing_creation, "_brand_input", lambda _page: FakeInput())
    monkeypatch.setattr(listing_creation, "_click_check_brand", lambda _page: None)
    monkeypatch.setattr(listing_creation, "_visible_text_candidates", lambda _page: ["Generic", "Samsung"])
    monkeypatch.setattr(
        listing_creation,
        "choose_brand_candidate",
        lambda *_args, **_kwargs: "Generic",
    )
    monkeypatch.setattr(listing_creation, "_body_text", lambda _page: body())
    monkeypatch.setattr(listing_creation, "_brand_confirmation_button", lambda _page: confirmation_button)
    monkeypatch.setattr(
        listing_creation,
        "_current_target_values",
        lambda current: ("Vehicle Camera System", "Generic") if current.phase == "product" else ("", ""),
    )


def test_brand_click_can_enter_product_info_step_directly(monkeypatch):
    page = StatePage("brand")
    _brand_test_setup(monkeypatch, page, body=lambda: "", confirmation_button=None)
    monkeypatch.setattr(
        listing_creation,
        "_click_exact_visible_text",
        lambda current, _selected: setattr(current, "phase", "product") or True,
    )

    selected = select_brand(
        page,
        FakeProvider({}),
        ListingBootstrapHints(("camera",), "", "unbranded", "camera"),
        wait_ms=0,
    )

    assert selected == "Generic"
    assert page.phase == "product"


def test_brand_confirmation_clicks_exact_confirm_button_before_step3(monkeypatch):
    page = StatePage("brand")
    button = FakeButton(lambda: setattr(page, "phase", "product"))
    _brand_test_setup(
        monkeypatch,
        page,
        body=lambda: "Selected Brand Generic\nConfirm Brand",
        confirmation_button=button,
    )
    monkeypatch.setattr(
        listing_creation,
        "_click_exact_visible_text",
        lambda current, _selected: setattr(current, "phase", "brand_confirmation") or True,
    )

    selected = select_brand(
        page,
        FakeProvider({}),
        ListingBootstrapHints(("camera",), "", "unbranded", "camera"),
        wait_ms=0,
    )

    assert selected == "Generic"
    assert button.clicks == 1
    assert page.phase == "product"


def test_brand_confirmation_rejects_mismatched_selected_brand(monkeypatch):
    page = StatePage("brand")
    button = FakeButton(lambda: None)
    _brand_test_setup(
        monkeypatch,
        page,
        body=lambda: "Selected Brand Samsung\nConfirm Brand",
        confirmation_button=button,
    )
    monkeypatch.setattr(
        listing_creation,
        "_click_exact_visible_text",
        lambda current, _selected: setattr(current, "phase", "brand_confirmation") or True,
    )

    with pytest.raises(RuntimeError, match="brand confirmation mismatch"):
        select_brand(
            page,
            FakeProvider({}),
            ListingBootstrapHints(("camera",), "", "unbranded", "camera"),
            wait_ms=0,
        )
    assert button.clicks == 0


def test_brand_confirmation_without_final_button_fails_clearly(monkeypatch):
    page = StatePage("brand")
    _brand_test_setup(
        monkeypatch,
        page,
        body=lambda: "Selected Brand Generic",
        confirmation_button=None,
    )
    monkeypatch.setattr(
        listing_creation,
        "_click_exact_visible_text",
        lambda current, _selected: setattr(current, "phase", "brand_confirmation") or True,
    )

    with pytest.raises(RuntimeError, match="no exact Select/Confirm/Use Brand button"):
        select_brand(
            page,
            FakeProvider({}),
            ListingBootstrapHints(("camera",), "", "unbranded", "camera"),
            wait_ms=0,
        )
