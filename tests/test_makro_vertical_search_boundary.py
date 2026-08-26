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


def _hints() -> vertical_selection.ListingBootstrapHints:
    return vertical_selection.ListingBootstrapHints(
        ("rain showerhead",), "SparkPod", "explicit", "high pressure rain showerhead"
    )


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


def test_vertical_search_samples_full_ladder_before_global_decision() -> None:
    source = inspect.getsource(vertical_selection._try_select_via_search)
    run_source = inspect.getsource(vertical_selection._run_vertical_search_query)
    assert "_run_vertical_search_query(" in source
    assert "merge_vertical_search_observations(observations)" in source
    assert "choose_vertical_candidate_pool(" in source
    assert "matched_queries_for_candidate(" in source
    assert "begin_search_query(search)" in run_source
    assert "generation <= 0" in run_source
    assert "_wait_for_scoped_vertical_search_candidates(" in run_source
    assert "allow_stable_exact=False" in source
    assert "_replay_grounded_vertical_candidate" not in source


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


def test_global_choice_on_active_generation_clicks_without_requery(monkeypatch) -> None:
    selected = "Home Improvement Tools / Bathroom Fittings & Sanitary / Shower Head"
    distractor = "Home & Kitchen / Bathroom Accessories / Soap Dishes"
    search = FakeSearch()
    calls: list[str] = []
    clicks: list[tuple[str, bool]] = []
    planned = ("rain showerhead", "showerhead")

    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(vertical_selection, "plan_vertical_search_terms", lambda *_args: planned)

    def run_query(_page, _search, term, *, wait_ms):
        _ = wait_ms
        calls.append(term)
        return [distractor] if term == planned[0] else [selected]

    monkeypatch.setattr(vertical_selection, "_run_vertical_search_query", run_query)
    monkeypatch.setattr(vertical_selection, "choose_vertical_candidate_pool", lambda *_args: selected)

    def click(_search, label, *, allow_stable_exact=False):
        clicks.append((label, allow_stable_exact))
        return True

    monkeypatch.setattr(vertical_selection, "click_search_row", click)
    monkeypatch.setattr(vertical_selection, "_current_target_values", lambda _page: ("", ""))
    monkeypatch.setattr(vertical_selection, "_complete_exact_live_vertical", lambda *_args, **_kwargs: "shower_head")

    resolved, observed, terms = vertical_selection._try_select_via_search(
        FakePage(), FakeProvider(), _hints(), wait_ms=80
    )

    assert resolved == "shower_head"
    assert terms == planned
    assert calls == list(planned)
    assert observed == [distractor, selected]
    assert clicks == [(selected, False)]


def test_global_choice_from_prior_owner_rebinds_only_that_owner(monkeypatch) -> None:
    selected = "Home Improvement Tools / Bathroom Fittings & Sanitary / Shower Head"
    distractor = "Home & Kitchen / Bathroom Accessories / Soap Dishes"
    search = FakeSearch()
    planned = ("rain showerhead", "bathroom fixture")
    calls: list[str] = []
    clicks: list[tuple[str, bool]] = []

    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(vertical_selection, "plan_vertical_search_terms", lambda *_args: planned)

    def run_query(_page, _search, term, *, wait_ms):
        _ = wait_ms
        calls.append(term)
        return [selected] if term == planned[0] else [distractor]

    monkeypatch.setattr(vertical_selection, "_run_vertical_search_query", run_query)
    monkeypatch.setattr(vertical_selection, "choose_vertical_candidate_pool", lambda *_args: selected)

    def click(_search, label, *, allow_stable_exact=False):
        clicks.append((label, allow_stable_exact))
        return True

    monkeypatch.setattr(vertical_selection, "click_search_row", click)
    monkeypatch.setattr(vertical_selection, "_current_target_values", lambda _page: ("", ""))
    monkeypatch.setattr(vertical_selection, "_complete_exact_live_vertical", lambda *_args, **_kwargs: "shower_head")

    resolved, _observed, _terms = vertical_selection._try_select_via_search(
        FakePage(), FakeProvider(), _hints(), wait_ms=80
    )

    assert resolved == "shower_head"
    assert calls == [planned[0], planned[1], planned[0]]
    assert clicks == [(selected, False)]


def test_current_generation_bind_failure_never_replays_same_owner(monkeypatch) -> None:
    selected = "Home Improvement Tools / Bathroom Fittings & Sanitary / Shower Head"
    search = FakeSearch()
    calls: list[str] = []

    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(vertical_selection, "plan_vertical_search_terms", lambda *_args: ("rain showerhead",))

    def run_query(_page, _search, term, *, wait_ms):
        _ = wait_ms
        calls.append(term)
        return [selected]

    monkeypatch.setattr(vertical_selection, "_run_vertical_search_query", run_query)
    monkeypatch.setattr(vertical_selection, "choose_vertical_candidate_pool", lambda *_args: selected)
    monkeypatch.setattr(vertical_selection, "_current_target_values", lambda _page: ("", ""))
    monkeypatch.setattr(vertical_selection, "click_search_row", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="could not be bound"):
        vertical_selection._try_select_via_search(FakePage(), FakeProvider(), _hints(), wait_ms=80)

    assert calls == ["rain showerhead"]


def test_failed_active_bind_can_rebind_a_distinct_prior_owner(monkeypatch) -> None:
    selected = "Home Improvement Tools / Bathroom Fittings & Sanitary / Shower Head"
    search = FakeSearch()
    planned = ("rain showerhead", "showerhead")
    calls: list[str] = []
    click_results = iter((False, True))
    clicks: list[str] = []

    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(vertical_selection, "plan_vertical_search_terms", lambda *_args: planned)

    def run_query(_page, _search, term, *, wait_ms):
        _ = wait_ms
        calls.append(term)
        return [selected]

    monkeypatch.setattr(vertical_selection, "_run_vertical_search_query", run_query)
    monkeypatch.setattr(vertical_selection, "choose_vertical_candidate_pool", lambda *_args: selected)
    monkeypatch.setattr(vertical_selection, "_current_target_values", lambda _page: ("", ""))

    def click(_search, label, *, allow_stable_exact=False):
        assert allow_stable_exact is False
        clicks.append(label)
        return next(click_results)

    monkeypatch.setattr(vertical_selection, "click_search_row", click)
    monkeypatch.setattr(vertical_selection, "_complete_exact_live_vertical", lambda *_args, **_kwargs: "shower_head")

    resolved, _observed, _terms = vertical_selection._try_select_via_search(
        FakePage(), FakeProvider(), _hints(), wait_ms=80
    )

    assert resolved == "shower_head"
    assert calls == [planned[0], planned[1], planned[0]]
    assert clicks == [selected, selected]


def test_duplicate_exact_active_rows_fail_closed(monkeypatch) -> None:
    selected = "Home Improvement Tools / Bathroom Fittings & Sanitary / Shower Head"
    search = FakeSearch()
    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(vertical_selection, "plan_vertical_search_terms", lambda *_args: ("rain showerhead",))
    monkeypatch.setattr(
        vertical_selection,
        "_run_vertical_search_query",
        lambda *_args, **_kwargs: [selected, selected],
    )
    monkeypatch.setattr(vertical_selection, "choose_vertical_candidate_pool", lambda *_args: selected)

    with pytest.raises(RuntimeError, match="could not be bound"):
        vertical_selection._try_select_via_search(FakePage(), FakeProvider(), _hints(), wait_ms=80)


def test_empty_global_decision_closes_search_and_falls_back_without_click(monkeypatch) -> None:
    search = FakeSearch()
    closed: list[bool] = []
    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(vertical_selection, "plan_vertical_search_terms", lambda *_args: ("rain showerhead", "showerhead"))
    monkeypatch.setattr(
        vertical_selection,
        "_run_vertical_search_query",
        lambda *_args, **_kwargs: ["Home & Kitchen / Bathroom Accessories / Soap Dishes"],
    )
    monkeypatch.setattr(vertical_selection, "choose_vertical_candidate_pool", lambda *_args: "")
    monkeypatch.setattr(vertical_selection, "_close_vertical_search", lambda *_args, **_kwargs: closed.append(True))
    monkeypatch.setattr(
        vertical_selection,
        "click_search_row",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not click")),
    )

    resolved, _observed, terms = vertical_selection._try_select_via_search(
        FakePage(), FakeProvider(), _hints(), wait_ms=80
    )
    assert resolved == ""
    assert terms == ("rain showerhead", "showerhead")
    assert closed == [True]


def test_select_vertical_searches_before_mutating_taxonomy() -> None:
    source = inspect.getsource(vertical_selection.select_vertical)
    assert source.index("_try_select_via_search(") < source.index("ResilientMakroTaxonomyBrowser(page)")
    assert "Taxonomy is a semantic fallback only" in source
