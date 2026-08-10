from __future__ import annotations

import pytest

from app.browser_page_owner import (
    BrowserPageOwnershipError,
    find_page_by_target_id,
    page_target_id,
)


class _Session:
    def __init__(self, target_id: str) -> None:
        self.target_id = target_id
        self.detached = False

    def send(self, method: str):
        assert method == "Target.getTargetInfo"
        return {"targetInfo": {"targetId": self.target_id}}

    def detach(self) -> None:
        self.detached = True


class _Context:
    def __init__(self) -> None:
        self.pages = []

    def new_cdp_session(self, page):
        return _Session(page.target_id)


class _Page:
    def __init__(self, context: _Context, target_id: str, *, closed: bool = False) -> None:
        self.context = context
        self.target_id = target_id
        self.closed = closed

    def is_closed(self) -> bool:
        return self.closed


def test_page_target_id_comes_from_chromium_target_info() -> None:
    context = _Context()
    page = _Page(context, "target-A")
    assert page_target_id(page) == "target-A"


def test_find_page_by_target_id_resolves_exactly_one_open_page() -> None:
    context = _Context()
    page_a = _Page(context, "target-A")
    page_b = _Page(context, "target-B")
    closed = _Page(context, "target-C", closed=True)
    context.pages = [page_a, page_b, closed]

    assert find_page_by_target_id(context, "target-B") is page_b

    with pytest.raises(BrowserPageOwnershipError):
        find_page_by_target_id(context, "target-missing")


def test_find_page_by_target_id_fails_closed_on_duplicate_ownership() -> None:
    context = _Context()
    context.pages = [
        _Page(context, "duplicate"),
        _Page(context, "duplicate"),
    ]
    with pytest.raises(BrowserPageOwnershipError):
        find_page_by_target_id(context, "duplicate")
