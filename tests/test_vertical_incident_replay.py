from __future__ import annotations

from app.makro import vertical_selection


class _FakeProvider:
    pass


class _FakePage:
    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class _FakeSearch:
    pass


def test_dyson_incident_selects_current_live_row_and_never_replays_owner_query(monkeypatch) -> None:
    selected = "Health & Beauty / Hair Care / Electric Hair Curlers"
    unrelated = "Home Improvement Tools / Hardware & Electricals / Electrical Plugs"
    planned = (
        "Dyson Airwrap HS05 Complete Long",
        "Dyson Airwrap multi-styler long",
        "Dyson hot air styler",
        "Dyson curling iron set",
        "Dyson Airwrap",
        "electric hair curler",
        "hair curler",
    )
    search = _FakeSearch()
    calls: list[str] = []
    decisions: list[str] = []
    clicked: list[str] = []

    hints = vertical_selection.ListingBootstrapHints(
        ("electric hair curler",),
        "Dyson",
        "explicit",
        "Dyson Airwrap HS05 Complete Long wired electric hair curler set",
    )

    monkeypatch.setattr(vertical_selection, "plan_vertical_search_terms", lambda *_args: planned)

    def run_query(_page, term, *, wait_ms):
        _ = wait_ms
        calls.append(term)
        if term == "hair curler":
            raise AssertionError("the query after the AI selection must never run")
        rows = [selected, unrelated] if term == "electric hair curler" else [unrelated]
        return rows, search

    def choose(_provider, _hints, terms, _candidates):
        term = terms[0]
        decisions.append(term)
        return selected if term == "electric hair curler" else ""

    monkeypatch.setattr(vertical_selection, "_run_vertical_search_query", run_query)
    monkeypatch.setattr(vertical_selection, "choose_vertical_candidate_pool", choose)
    monkeypatch.setattr(vertical_selection, "_current_target_values", lambda _page: ("", ""))

    def click(_search, label, *, allow_stable_exact=False):
        assert allow_stable_exact is False
        clicked.append(label)
        return True

    monkeypatch.setattr(vertical_selection, "click_search_row", click)
    monkeypatch.setattr(
        vertical_selection,
        "_complete_exact_live_vertical",
        lambda _page, _label, **_kwargs: "electric_hair_curler",
    )

    resolved, observed, terms = vertical_selection._try_select_via_search(
        _FakePage(),
        _FakeProvider(),
        hints,
        wait_ms=50,
    )

    assert resolved == "electric_hair_curler"
    assert terms == planned
    assert calls == list(planned[:6])
    assert decisions == list(planned[:6])
    assert calls.count("electric hair curler") == 1
    assert "hair curler" not in calls
    assert selected in observed
    assert clicked == [selected]
