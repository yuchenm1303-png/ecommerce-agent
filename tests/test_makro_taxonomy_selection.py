from __future__ import annotations

import app.makro.listing_creation as listing_creation
from app.makro.listing_creation import ListingBootstrapHints, choose_taxonomy_candidate, select_vertical


class SequenceProvider:
    name = "fake"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def extract_json(self, request_payload):
        self.requests.append(request_payload)
        return self.responses.pop(0)


class FakeInput:
    def __init__(self):
        self.values = []

    def fill(self, value):
        self.values.append(value)


class FakeButton:
    def __init__(self, callback):
        self.callback = callback
        self.clicks = 0

    def click(self, **_kwargs):
        self.clicks += 1
        self.callback()


class TaxonomyPage:
    def __init__(self):
        self.phase = "vertical"
        self.level = 0
        self.url = "https://seller.makro.co.za/index.html#dashboard/addListings/single"

    def wait_for_timeout(self, _milliseconds):
        return None


def _hints():
    return ListingBootstrapHints(
        vertical_search_terms=("phone case",),
        brand="",
        brand_status="unbranded",
        product_summary="protective phone case with lens film",
        product_identity={
            "entity_kind": "physical_product",
            "product_type_en": "phone case",
        },
    )


def test_taxonomy_choice_can_only_return_one_exact_live_node():
    provider = SequenceProvider([{"selected_node": "Cases & Covers"}])
    selected = choose_taxonomy_candidate(
        provider,
        _hints(),
        ["Mobile, Tablets and Accessories", "Mobile & Tablet Accessories"],
        ["Batteries", "Cases & Covers", "Headphones"],
    )

    assert selected == "Cases & Covers"
    request = provider.requests[0]
    assert request["task"] == "choose_exact_makro_taxonomy_node"
    assert request["context"]["live_nodes"] == ["Batteries", "Cases & Covers", "Headphones"]
    assert request["context"]["current_path"] == [
        "Mobile, Tablets and Accessories",
        "Mobile & Tablet Accessories",
    ]


def test_select_vertical_walks_live_taxonomy_before_search(monkeypatch):
    page = TaxonomyPage()
    search = FakeInput()
    button = FakeButton(lambda: setattr(page, "phase", "brand"))

    levels = [
        ["Agricultural Products", "Automobile", "Mobile, Tablets and Accessories", "Baby Care"],
        ["E Reader", "Mobile", "Mobile & Tablet Accessories", "Tablet"],
        ["Batteries", "Cases & Covers", "Data Cables", "Headphones"],
    ]

    def columns(_page):
        return [list(levels[index]) for index in range(min(page.level + 1, len(levels)))]

    choices = {
        (): "Mobile, Tablets and Accessories",
        ("Mobile, Tablets and Accessories",): "Mobile & Tablet Accessories",
        (
            "Mobile, Tablets and Accessories",
            "Mobile & Tablet Accessories",
        ): "Cases & Covers",
    }

    def choose(_provider, _hints, path, _candidates):
        return choices[tuple(path)]

    def click(_page, level, selected):
        if level < 2:
            page.level = level + 1
        else:
            page.phase = "vertical_confirmation"
        return True

    monkeypatch.setattr(listing_creation, "is_vertical_step", lambda current: current.phase.startswith("vertical"))
    monkeypatch.setattr(listing_creation, "is_brand_step", lambda current: current.phase == "brand")
    monkeypatch.setattr(listing_creation, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(listing_creation, "_taxonomy_columns", columns)
    monkeypatch.setattr(listing_creation, "choose_taxonomy_candidate", choose)
    monkeypatch.setattr(listing_creation, "_click_taxonomy_node", click)
    monkeypatch.setattr(
        listing_creation,
        "_vertical_confirmation_content",
        lambda current: current.phase == "vertical_confirmation",
    )
    monkeypatch.setattr(listing_creation, "_vertical_select_brand_button", lambda _page: button)
    monkeypatch.setattr(
        listing_creation,
        "_current_target_values",
        lambda current: ("Cases & Covers", "") if current.phase in {"vertical_confirmation", "brand"} else ("", ""),
    )
    monkeypatch.setattr(listing_creation, "_selected_value_verified", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        listing_creation,
        "_select_vertical_via_search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("search fallback should not run")),
    )

    selected = select_vertical(page, SequenceProvider([]), _hints(), wait_ms=0)

    assert selected == "Cases & Covers"
    assert page.phase == "brand"
    assert button.clicks == 1
    assert search.values == [""]
