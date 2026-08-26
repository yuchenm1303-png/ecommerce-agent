from __future__ import annotations

import json
from pathlib import Path

from app.makro import vertical_selection

_FIXTURE = Path(__file__).parent / "fixtures" / "regressions" / "ultrasonic_cleaner_current_generation.json"


class _FakeProvider:
    def extract_json(self, payload):
        raise AssertionError("incident replay must not call paid or fake AI; chooser is replayed deterministically")


class _FakePage:
    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class _FakeSearch:
    pass


def test_ultrasonic_cleaner_incident_does_not_repeat_active_owner_query(monkeypatch) -> None:
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    planned = tuple(fixture["planned_terms"])
    observations = {key: list(value) for key, value in fixture["observations"].items()}
    selected = fixture["historical_selected_vertical"]
    calls: list[str] = []
    clicks: list[tuple[str, bool]] = []

    hints = vertical_selection.ListingBootstrapHints(
        (fixture["product_type"],),
        "",
        "unknown",
        fixture["product_type"],
    )

    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: _FakeSearch())
    monkeypatch.setattr(vertical_selection, "plan_vertical_search_terms", lambda _provider, _hints: planned)

    def run_query(_page, _search, term, *, wait_ms):
        _ = wait_ms
        calls.append(term)
        return list(observations[term])

    monkeypatch.setattr(vertical_selection, "_run_vertical_search_query", run_query)
    monkeypatch.setattr(
        vertical_selection,
        "choose_vertical_candidate_pool",
        lambda _provider, _hints, terms, pool: selected,
    )

    def click(_search, label, *, allow_stable_exact=False):
        clicks.append((label, allow_stable_exact))
        return True

    monkeypatch.setattr(vertical_selection, "click_search_row", click)
    monkeypatch.setattr(vertical_selection, "_current_target_values", lambda _page: ("", ""))
    monkeypatch.setattr(
        vertical_selection,
        "_complete_exact_live_vertical",
        lambda _page, _label, **_kwargs: "lens_cleaner",
    )

    resolved, observed, terms = vertical_selection._try_select_via_search(
        _FakePage(),
        _FakeProvider(),
        hints,
        wait_ms=50,
    )

    assert resolved == "lens_cleaner"
    assert terms == planned
    assert calls == list(planned)
    assert calls.count(fixture["expected_owner_query"]) == 1
    assert observed[-1] == selected
    assert clicks == [(selected, False)]
