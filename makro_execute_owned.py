"""Canonical GUI host for one real Makro execution.

The business executor stays in ``makro_execute_listing``. This host owns the
cross-process CDP transport lane in the *same process* as that executor, so both
source-Python runs and the installed ``EcommerceAgentWorker.exe`` follow exactly
the same ownership path. It never spawns another Python/executable child.

Batch execution also reconciles the exact owned Chromium tab before handing
control to the business executor. If that tab drifted while waiting for the CDP
lane, recovery is allowed only through an existing history entry whose short-
lived Makro draft identity exactly matches the live schema prepared for this
job. It never navigates another tab and never replays a stale listing URL.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.browser_page_owner import find_page_by_target_id
from app.browser_session import DEFAULT_CDP_PORT, _connect_browser_resilient
from app.cdp_automation_health import poison_matches_current_generation
from app.cdp_transport_lane import exclusive_cdp_transport_lane
from app.makro.listing import is_makro_listing_page
from app.makro.listing_draft_identity import (
    listing_draft_identity_from_url,
    normalized_listing_draft_identity,
)
from makro_execute_listing import main as execute_main


_OWNED_HISTORY_SETTLE_MS = 8_000
_OWNED_HISTORY_POLL_MS = 250


def _cdp_port(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parsed, _unknown = parser.parse_known_args([str(value) for value in argv])
    return int(parsed.cdp_port)


def _owned_execution_args(argv: Sequence[str]) -> tuple[str, str]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--makro-target-id", default="")
    parser.add_argument("--live-schema", default="")
    parsed, _unknown = parser.parse_known_args([str(value) for value in argv])
    return (
        str(parsed.makro_target_id or "").strip(),
        str(parsed.live_schema or "").strip(),
    )


def _prepared_draft_identity(live_schema: str) -> dict[str, str] | None:
    path = Path(str(live_schema or "").strip())
    if not str(path) or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Batch prepared live schema could not be read for owned-tab recovery: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        return None
    return normalized_listing_draft_identity(payload.get("listing_draft_identity"))


def _history_identity(url: object) -> dict[str, str] | None:
    try:
        return listing_draft_identity_from_url(str(url or "").strip())
    except (RuntimeError, ValueError):
        return None


def _matching_owned_history_entries(
    history: object,
    expected_identity: dict[str, str],
) -> list[dict[str, Any]]:
    """Return exact prepared-draft entries, nearest to the current history slot first."""

    if not isinstance(history, dict):
        return []
    entries = history.get("entries")
    if not isinstance(entries, list):
        return []
    try:
        current_index = int(history.get("currentIndex"))
    except (TypeError, ValueError):
        current_index = len(entries) - 1

    matches: list[tuple[int, int, dict[str, Any]]] = []
    for index, raw in enumerate(entries):
        if index == current_index or not isinstance(raw, dict):
            continue
        identity = _history_identity(raw.get("url"))
        if identity != expected_identity:
            continue
        try:
            entry_id = int(raw.get("id"))
        except (TypeError, ValueError):
            continue
        matches.append((abs(index - current_index), index, {"id": entry_id, "url": str(raw.get("url") or "")}))

    matches.sort(key=lambda item: (item[0], -item[1]))
    return [item[2] for item in matches]


def _page_matches_prepared_draft(page: Any, expected_identity: dict[str, str]) -> bool:
    if not is_makro_listing_page(page):
        return False
    return _history_identity(page.url) == expected_identity


def _reconcile_owned_listing_draft(
    port: int,
    target_id: str,
    live_schema: str,
) -> bool:
    """Restore only the exact owned tab and exact prepare-time Makro draft.

    Makro requestId/vid values are short-lived and are never used for a fresh
    ``goto``. They are used only as ownership evidence while selecting an
    already-existing Chromium history entry in the same target. The production
    executor still performs its normal vertical/live-schema preflight after this
    function returns.
    """

    expected_identity = _prepared_draft_identity(live_schema)
    with sync_playwright() as playwright:
        browser = _connect_browser_resilient(playwright, int(port))
        contexts = list(browser.contexts)
        if not contexts:
            raise RuntimeError("Batch owned-tab recovery connected to Edge without a browser context")
        page = find_page_by_target_id(contexts[0], target_id)

        if expected_identity is None:
            # Backward-compatible fail-closed behavior: an already-correct listing
            # may proceed to the executor, but an off-route tab cannot be restored
            # without prepare-time draft ownership evidence.
            return bool(is_makro_listing_page(page))

        if _page_matches_prepared_draft(page, expected_identity):
            return True

        session = page.context.new_cdp_session(page)
        try:
            history = session.send("Page.getNavigationHistory")
            candidates = _matching_owned_history_entries(history, expected_identity)
            for attempt, candidate in enumerate(candidates[:4], start=1):
                print(
                    "BATCH_OWNED_TAB RECOVERY_ATTEMPT "
                    f"target_id={target_id} attempt={attempt}/{min(4, len(candidates))} "
                    f"history_entry_id={candidate['id']}",
                    flush=True,
                )
                session.send("Page.navigateToHistoryEntry", {"entryId": int(candidate["id"])})
                polls = max(1, _OWNED_HISTORY_SETTLE_MS // _OWNED_HISTORY_POLL_MS)
                for _ in range(polls):
                    if page.is_closed():
                        return False
                    if _page_matches_prepared_draft(page, expected_identity):
                        print(
                            "BATCH_OWNED_TAB RECOVERED "
                            f"target_id={target_id} history_entry_id={candidate['id']}",
                            flush=True,
                        )
                        return True
                    page.wait_for_timeout(_OWNED_HISTORY_POLL_MS)
            return False
        finally:
            try:
                session.detach()
            except Exception:
                pass


def main() -> int:
    argv = [str(value) for value in sys.argv[1:]]
    port = _cdp_port(argv)
    target_id, live_schema = _owned_execution_args(argv)
    with exclusive_cdp_transport_lane(port):
        if poison_matches_current_generation(port):
            raise RuntimeError(
                "Makro Browser automation generation 已标记失效；"
                "真实执行不会继续 attach，等待 GUI 在空闲边界安全恢复。"
            )
        if target_id and not _reconcile_owned_listing_draft(port, target_id, live_schema):
            raise RuntimeError(
                "Batch owned Makro tab drifted away from its prepared Add Listing draft, "
                "and that exact draft could not be restored from the same tab history; "
                "refusing to navigate another tab or replay a stale listing URL."
            )
        return int(execute_main())


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
