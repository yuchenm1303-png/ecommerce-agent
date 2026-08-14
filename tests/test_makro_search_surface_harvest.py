from __future__ import annotations

import app.makro.search_surface as search_surface


class FakePage:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(int(milliseconds))


class FakeSearch:
    pass


def test_wait_for_search_rows_harvests_every_scroll_page_and_restores_top(monkeypatch) -> None:
    page = FakePage()
    search = FakeSearch()
    state = {"position": 0, "resets": 0, "moves": 0}
    pages = {
        0: ["Category / First", "Category / Second"],
        1: ["Category / Second", "Category / Third"],
        2: ["Category / Third", "Category / Fourth"],
    }

    monkeypatch.setattr(
        search_surface,
        "read_search_rows",
        lambda _search: list(pages[state["position"]]),
    )

    def move(_search, *, reset: bool = False):
        if reset:
            state["position"] = 0
            state["resets"] += 1
            return {
                "found": True,
                "moved": True,
                "at_end": False,
                "scroll_top": 0,
                "max_scroll": 200,
            }
        if state["position"] >= 2:
            return {
                "found": True,
                "moved": False,
                "at_end": True,
                "scroll_top": 200,
                "max_scroll": 200,
            }
        state["position"] += 1
        state["moves"] += 1
        return {
            "found": True,
            "moved": True,
            "at_end": state["position"] >= 2,
            "scroll_top": state["position"] * 100,
            "max_scroll": 200,
        }

    monkeypatch.setattr(search_surface, "_move_search_surface", move)

    rows = search_surface.wait_for_search_rows(
        page,
        search,
        timeout_ms=500,
        poll_ms=50,
    )

    assert rows == [
        "Category / First",
        "Category / Second",
        "Category / Third",
        "Category / Fourth",
    ]
    assert state["moves"] == 2
    assert state["resets"] == 2
    assert state["position"] == 0


def test_harvest_stops_when_surface_cannot_scroll(monkeypatch) -> None:
    page = FakePage()
    search = FakeSearch()
    resets = []

    monkeypatch.setattr(
        search_surface,
        "read_search_rows",
        lambda _search: ["Only / Visible / Result"],
    )

    def move(_search, *, reset: bool = False):
        resets.append(reset)
        return {
            "found": False,
            "moved": False,
            "at_end": True,
            "scroll_top": 0,
            "max_scroll": 0,
        }

    monkeypatch.setattr(search_surface, "_move_search_surface", move)

    rows = search_surface.harvest_search_rows(page, search, poll_ms=50)

    assert rows == ["Only / Visible / Result"]
    assert resets == [True, False, True]
