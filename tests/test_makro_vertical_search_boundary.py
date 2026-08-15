from __future__ import annotations

import inspect

import pytest

from app.makro import vertical_selection


class FakeProvider:
    def __init__(self, response=None) -> None:
        self.response = response or {"selected_vertical": ""}
        self.requests = []

    def extract_json(self, payload):
        self.requests.append(payload)
        return self.response


class FakePage:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(int(milliseconds))


class FakeSearch:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.generation = 0

    def fill(self, value: str) -> None:
        self.events.append(("fill", value))

    def press(self, key: str) -> None:
        self.events.append(("press", key))

    def evaluate(self, expression: str, *_args):
        self.events.append(("evaluate", expression))
        if expression == "el => el.blur()":
            return None
        self.generation += 1
        return {"generation": self.generation}


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


def test_vertical_search_uses_generation_owned_surface_only() -> None:
    source = inspect.getsource(vertical_selection._try_select_via_search)
    run_source = inspect.getsource(vertical_selection._run_vertical_search_query)
    assert "_run_vertical_search_query(" in source
    assert "begin_search_query(search)" in run_source
    assert "generation <= 0" in run_source
    assert "_wait_for_scoped_vertical_search_candidates(" in run_source
    assert "_search_result_delta(" not in source
    assert "_visible_text_candidates" not in source


def test_query_reset_never_requires_old_dom_to_disappear() -> None:
    page = FakePage()
    search = FakeSearch()

    assert vertical_selection._close_vertical_search(search, page, wait_ms=800) is None
    assert search.events[:3] == [
        ("fill", ""),
        ("press", "Escape"),
        ("evaluate", "el => el.blur()"),
    ]
    assert page.waits
    source = inspect.getsource(vertical_selection._close_vertical_search)
    assert "query_quiescence" not in source
    assert "remaining_rows" not in source
    assert "DOM disappearance is never used" in source


def test_each_discovery_query_starts_fresh_generation_after_reset(monkeypatch) -> None:
    page = FakePage()
    search = FakeSearch()
    order: list[str] = []

    monkeypatch.setattr(
        vertical_selection,
        "_close_vertical_search",
        lambda *_args, **_kwargs: order.append("reset"),
    )
    monkeypatch.setattr(
        vertical_selection,
        "begin_search_query",
        lambda _search: order.append("generation") or 7,
    )
    monkeypatch.setattr(
        vertical_selection,
        "_wait_for_scoped_vertical_search_candidates",
        lambda *_args, **_kwargs: ["Home Improvement Tools / Bathroom Fittings & Sanitary / Shower Head"],
    )

    rows = vertical_selection._run_vertical_search_query(
        page,
        search,
        "rain showerhead",
        wait_ms=800,
    )
    assert order == ["reset", "generation"]
    assert search.events[0] == ("fill", "rain showerhead")
    assert rows == ["Home Improvement Tools / Bathroom Fittings & Sanitary / Shower Head"]


def test_generation_creation_failure_stops_before_query_write(monkeypatch) -> None:
    page = FakePage()
    search = FakeSearch()
    monkeypatch.setattr(vertical_selection, "_close_vertical_search", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(vertical_selection, "begin_search_query", lambda _search: 0)

    with pytest.raises(RuntimeError, match="ownership generation"):
        vertical_selection._run_vertical_search_query(
            page,
            search,
            "rain showerhead",
            wait_ms=800,
        )
    assert ("fill", "rain showerhead") not in search.events


def test_grounded_replay_can_bind_stable_exact_row_without_fresh_mutation(monkeypatch) -> None:
    selected = "Home Improvement Tools / Bathroom Fittings & Sanitary / Shower Head"
    page = FakePage()
    search = FakeSearch()
    seen: dict[str, object] = {}

    monkeypatch.setattr(vertical_selection, "_run_vertical_search_query", lambda *_args, **_kwargs: [])

    def click(_search, label, *, allow_stable_exact=False):
        seen["label"] = label
        seen["allow_stable_exact"] = allow_stable_exact
        return True

    monkeypatch.setattr(vertical_selection, "click_search_row", click)
    clicked, rows = vertical_selection._replay_grounded_vertical_candidate(
        page,
        search,
        term="rain showerhead",
        selected=selected,
        wait_ms=800,
    )
    assert clicked is True
    assert rows == []
    assert seen == {"label": selected, "allow_stable_exact": True}


def test_grounded_live_candidate_replay_failure_refuses_taxonomy_fallback(monkeypatch) -> None:
    selected = "Home Improvement Tools / Bathroom Fittings & Sanitary / Shower Head"
    calls: list[str] = []
    search = FakeSearch()
    provider = FakeProvider()
    hints = vertical_selection.ListingBootstrapHints(
        ("rain showerhead",), "SparkPod", "explicit", "high pressure rain showerhead"
    )

    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(
        vertical_selection,
        "plan_vertical_search_terms",
        lambda _provider, _hints: ("rain showerhead", "showerhead"),
    )

    def run_query(_page, _search, term, *, wait_ms):
        _ = wait_ms
        calls.append(term)
        if len(calls) == 1:
            return [selected]
        return []

    monkeypatch.setattr(vertical_selection, "_run_vertical_search_query", run_query)
    monkeypatch.setattr(
        vertical_selection,
        "choose_vertical_candidate_pool",
        lambda *_args, **_kwargs: selected,
    )
    monkeypatch.setattr(vertical_selection, "click_search_row", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(vertical_selection, "_close_vertical_search", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="refusing taxonomy fallback"):
        vertical_selection._try_select_via_search(
            FakePage(),
            provider,
            hints,
            wait_ms=800,
        )

    # Two discovery generations followed only by the original query that grounded
    # the selected candidate. No unrelated taxonomy mechanism is entered here.
    assert calls == ["rain showerhead", "showerhead", "rain showerhead"]


def test_select_vertical_searches_before_mutating_taxonomy() -> None:
    source = inspect.getsource(vertical_selection.select_vertical)
    assert source.index("_try_select_via_search(") < source.index("ResilientMakroTaxonomyBrowser(page)")
    assert "Taxonomy is a semantic fallback only" in source
