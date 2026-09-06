from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import makro_execute_owned as owned


EXPECTED = {
    "request_id": "REQ-EXPECTED",
    "vid": "1792",
    "vertical": "hand_blender",
    "brand": "vincie",
}


def _listing_url(request_id: str, *, vid: str = "1792") -> str:
    return (
        "https://seller.makro.co.za/index.html#dashboard/addListings/single"
        f"?brand=VINCIE&vertical=hand_blender&requestId={request_id}"
        f"&context=CPUI&firstDraft=1&vid={vid}"
    )


def _write_schema(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "listing_draft_identity": EXPECTED,
                "fields": [],
            }
        ),
        encoding="utf-8",
    )
    return path


class _FakeSession:
    def __init__(self, page: "_FakePage", history: dict[str, object]) -> None:
        self.page = page
        self.history = history
        self.calls: list[tuple[str, dict[str, object] | None]] = []
        self.detached = False

    def send(self, method: str, params: dict[str, object] | None = None):
        self.calls.append((method, params))
        if method == "Page.getNavigationHistory":
            return self.history
        if method == "Page.navigateToHistoryEntry":
            entry_id = int((params or {})["entryId"])
            for entry in self.history["entries"]:  # type: ignore[index]
                if int(entry["id"]) == entry_id:
                    self.page.url = str(entry["url"])
                    return {}
            raise AssertionError(f"unknown history entry {entry_id}")
        raise AssertionError(f"unexpected CDP method {method}")

    def detach(self) -> None:
        self.detached = True


class _FakeContext:
    def __init__(self, page: "_FakePage", history: dict[str, object]) -> None:
        self.pages = [page]
        self.session = _FakeSession(page, history)

    def new_cdp_session(self, _page: "_FakePage") -> _FakeSession:
        return self.session


class _FakePage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.context: _FakeContext
        self.waits: list[int] = []

    def is_closed(self) -> bool:
        return False

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(int(milliseconds))


class _FakePlaywrightContext:
    def __enter__(self):
        return object()

    def __exit__(self, *_args) -> None:
        return None


def test_matching_history_entries_require_exact_prepare_time_draft_identity() -> None:
    history = {
        "currentIndex": 3,
        "entries": [
            {"id": 1, "url": _listing_url("REQ-EXPECTED")},
            {"id": 2, "url": _listing_url("REQ-OTHER")},
            {"id": 3, "url": "https://seller.makro.co.za/index.html#dashboard/home-page"},
            {"id": 4, "url": _listing_url("REQ-EXPECTED", vid="DIFFERENT")},
        ],
    }

    matches = owned._matching_owned_history_entries(history, EXPECTED)

    assert [item["id"] for item in matches] == [1]


def test_reconcile_restores_only_exact_owned_draft_history_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = "https://seller.makro.co.za/index.html#dashboard/home-page"
    history = {
        "currentIndex": 2,
        "entries": [
            {"id": 10, "url": _listing_url("REQ-EXPECTED")},
            {"id": 11, "url": _listing_url("REQ-OTHER")},
            {"id": 12, "url": home},
        ],
    }
    page = _FakePage(home)
    context = _FakeContext(page, history)
    page.context = context
    browser = SimpleNamespace(contexts=[context])
    schema = _write_schema(tmp_path / "live-schema.json")

    monkeypatch.setattr(owned, "sync_playwright", lambda: _FakePlaywrightContext())
    monkeypatch.setattr(owned, "_connect_browser_resilient", lambda *_args, **_kwargs: browser)
    monkeypatch.setattr(owned, "find_page_by_target_id", lambda _context, _target: page)
    monkeypatch.setattr(
        owned,
        "is_makro_listing_page",
        lambda current: owned._history_identity(current.url) is not None,
    )

    assert owned._reconcile_owned_listing_draft(9222, "TARGET-1", str(schema)) is True
    navigate_calls = [
        params
        for method, params in context.session.calls
        if method == "Page.navigateToHistoryEntry"
    ]
    assert navigate_calls == [{"entryId": 10}]
    assert owned._history_identity(page.url) == EXPECTED
    assert context.session.detached is True


def test_reconcile_refuses_other_listing_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = "https://seller.makro.co.za/index.html#dashboard/home-page"
    history = {
        "currentIndex": 1,
        "entries": [
            {"id": 20, "url": _listing_url("REQ-OTHER")},
            {"id": 21, "url": home},
        ],
    }
    page = _FakePage(home)
    context = _FakeContext(page, history)
    page.context = context
    browser = SimpleNamespace(contexts=[context])
    schema = _write_schema(tmp_path / "live-schema.json")

    monkeypatch.setattr(owned, "sync_playwright", lambda: _FakePlaywrightContext())
    monkeypatch.setattr(owned, "_connect_browser_resilient", lambda *_args, **_kwargs: browser)
    monkeypatch.setattr(owned, "find_page_by_target_id", lambda _context, _target: page)
    monkeypatch.setattr(owned, "is_makro_listing_page", lambda _page: False)

    assert owned._reconcile_owned_listing_draft(9222, "TARGET-1", str(schema)) is False
    assert not any(
        method == "Page.navigateToHistoryEntry" for method, _params in context.session.calls
    )
    assert page.url == home
