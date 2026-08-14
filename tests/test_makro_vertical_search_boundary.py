from __future__ import annotations

import inspect

from app.makro import vertical_selection


class FakeProvider:
    def __init__(self, response=None) -> None:
        self.response = response or {"selected_vertical": ""}
        self.requests = []

    def extract_json(self, payload):
        self.requests.append(payload)
        return self.response


def test_search_delta_rejects_preexisting_broad_taxonomy_nodes() -> None:
    before = ["Health & Beauty", "Bath and Spa", "Health Care Devices", "Personal Care & Grooming"]
    after = [*before, "Neck Massagers", "Massage Devices"]
    columns = [
        ["Furniture", "Gaming", "Health & Beauty", "Home & Kitchen"],
        ["Bath and Spa", "Health Care Devices", "Personal Care & Grooming"],
    ]
    assert vertical_selection._search_result_delta(before, after, columns) == [
        "Neck Massagers", "Massage Devices"
    ]


def test_search_breadcrumb_leaf_is_separate_from_exact_click_label() -> None:
    label = "Home Improvement Tools / Alternate Energy & Accessories / Solar Charge Controller"
    assert vertical_selection._search_result_leaf(label) == "Solar Charge Controller"


def test_unique_exact_breadcrumb_leaf_is_selected_without_ai_guessing() -> None:
    provider = FakeProvider()
    hints = vertical_selection.ListingBootstrapHints(
        ("solar charge controller",), "", "unknown", "solar charge controller"
    )
    candidates = [
        "Home Improvement Tools / Alternate Energy & Accessories / Solar Charge Controller",
        "TV, Audio & Video Players / Audio Accessories / Remote Controllers",
        "Gaming / Controllers / Motion Controllers",
    ]
    selected = vertical_selection._choose_vertical_search_candidate(
        provider, hints, "solar charge controller", candidates
    )
    assert selected == candidates[0]
    assert provider.requests == []


def test_duplicate_exact_leaf_does_not_guess_between_live_paths(monkeypatch) -> None:
    provider = FakeProvider()
    hints = vertical_selection.ListingBootstrapHints(
        ("solar charge controller",), "", "unknown", "solar charge controller"
    )
    candidates = [
        "Home Improvement / Alternate Energy / Solar Charge Controller",
        "Industrial Supplies / Renewable Energy / Solar Charge Controller",
    ]
    seen = {}

    def choose(_provider, _hints, term, live):
        seen["term"] = term
        seen["live"] = list(live)
        return candidates[1]

    monkeypatch.setattr(vertical_selection, "choose_vertical_candidate", choose)
    assert vertical_selection._choose_vertical_search_candidate(
        provider, hints, "solar charge controller", candidates
    ) == candidates[1]
    assert seen == {"term": "solar charge controller", "live": candidates}


def test_vertical_search_uses_query_owned_surface_only() -> None:
    source = inspect.getsource(vertical_selection._try_select_via_search)
    assert "begin_search_query(search)" in source
    assert "_wait_for_scoped_vertical_search_candidates(" in source
    assert "click_search_row(search, selected)" in source
    assert "_search_result_delta(" not in source
    assert "_visible_text_candidates" not in source


def test_select_vertical_searches_before_mutating_taxonomy() -> None:
    source = inspect.getsource(vertical_selection.select_vertical)
    assert source.index("_try_select_via_search(") < source.index("ResilientMakroTaxonomyBrowser(page)")
