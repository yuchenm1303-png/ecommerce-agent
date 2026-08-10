from __future__ import annotations

from typing import Any


class BrowserPageOwnershipError(RuntimeError):
    """Raised when a persisted browser page ownership token cannot be resolved."""


def page_target_id(page: Any) -> str:
    """Return Chromium's stable target id for one Playwright Page.

    The target id belongs to the browser tab, survives same-tab navigation, and
    gives batch workers a deterministic ownership key without relying on tab
    order, current focus, URL similarity, or visible text.
    """

    context = page.context
    session = context.new_cdp_session(page)
    try:
        payload = session.send("Target.getTargetInfo")
    finally:
        try:
            session.detach()
        except Exception:
            pass
    target = payload.get("targetInfo") or {}
    value = str(target.get("targetId") or "").strip()
    if not value:
        raise BrowserPageOwnershipError("Chromium page did not expose a targetId")
    return value


def find_page_by_target_id(context: Any, target_id: str):
    """Resolve exactly one currently-open Page from a persisted target id."""

    wanted = str(target_id or "").strip()
    if not wanted:
        raise BrowserPageOwnershipError("browser target id is empty")

    matches = []
    for page in list(context.pages):
        try:
            if not page.is_closed() and page_target_id(page) == wanted:
                matches.append(page)
        except Exception:
            continue
    if len(matches) != 1:
        raise BrowserPageOwnershipError(
            f"browser target {wanted!r} resolved to {len(matches)} open pages; expected exactly 1"
        )
    return matches[0]


__all__ = [
    "BrowserPageOwnershipError",
    "find_page_by_target_id",
    "page_target_id",
]
