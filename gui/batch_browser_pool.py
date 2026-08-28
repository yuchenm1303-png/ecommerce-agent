from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

from app.browser_session import (
    DEFAULT_START_URL,
    cdp_attach_guard,
    cdp_endpoint,
    is_cdp_ready,
    launch_detached_edge,
)


_MAKRO_HOST = "seller.makro.co.za"
_MAKRO_ORIGIN = f"https://{_MAKRO_HOST}"


@dataclass(slots=True, frozen=True)
class BatchBrowserLane:
    """One independently controllable Makro Edge process used by Batch workers."""

    index: int
    cdp_port: int
    profile_dir: Path

    @property
    def label(self) -> str:
        return f"lane-{self.index + 1:02d}"


def build_batch_browser_lanes(
    project_root: str | Path,
    *,
    base_port: int,
    count: int,
) -> tuple[BatchBrowserLane, ...]:
    """Build deterministic, non-overlapping browser lanes for one Batch.

    Lane 0 intentionally reuses the formal GUI's primary Makro profile/port so
    the user's existing login remains the seed session. Every additional lane
    owns a distinct profile directory and CDP port; therefore the existing
    per-port CdpSessionLease remains strict while different jobs can genuinely
    automate Makro at the same time.
    """

    root = Path(project_root).resolve()
    total = max(1, int(count))
    first_port = int(base_port)
    lanes: list[BatchBrowserLane] = []
    for index in range(total):
        profile = (
            root / "browser_profiles" / "makro-edge"
            if index == 0
            else root / "browser_profiles" / f"makro-edge-worker-{index + 1:02d}"
        )
        lanes.append(
            BatchBrowserLane(
                index=index,
                cdp_port=first_port + index,
                profile_dir=profile,
            )
        )
    return tuple(lanes)


def browser_lane_token(port: int) -> str:
    """Return the current external Edge instance token for one CDP lane."""

    try:
        with urllib.request.urlopen(
            f"{cdp_endpoint(int(port))}/json/version",
            timeout=0.6,
        ) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return str(payload.get("webSocketDebuggerUrl") or "").strip()
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return ""


def _connect_browser(playwright: Any, port: int):
    with cdp_attach_guard(int(port)):
        browser = playwright.chromium.connect_over_cdp(
            cdp_endpoint(int(port)),
            timeout=25_000,
        )
    contexts = list(browser.contexts)
    if not contexts:
        raise RuntimeError(f"Makro browser lane {int(port)} 没有可用 browser context。")
    return browser, contexts[0]


def _makro_cookie(cookie: dict[str, Any]) -> bool:
    domain = str(cookie.get("domain") or "").lstrip(".").casefold()
    return domain == "makro.co.za" or domain.endswith(".makro.co.za")


def _export_primary_login_state(source_port: int) -> dict[str, Any]:
    """Read only Makro cookie/localStorage login state from the primary lane."""

    with sync_playwright() as playwright:
        _browser, context = _connect_browser(playwright, int(source_port))
        state = context.storage_state()
        cookies = [
            dict(cookie)
            for cookie in state.get("cookies") or []
            if isinstance(cookie, dict) and _makro_cookie(cookie)
        ]
        local_storage: list[dict[str, str]] = []
        for origin in state.get("origins") or []:
            if not isinstance(origin, dict):
                continue
            if str(origin.get("origin") or "").rstrip("/").casefold() != _MAKRO_ORIGIN:
                continue
            for item in origin.get("localStorage") or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "")
                value = str(item.get("value") or "")
                if name:
                    local_storage.append({"name": name, "value": value})
        return {
            "cookies": cookies,
            "local_storage": local_storage,
        }


def _import_primary_login_state(lane: BatchBrowserLane, state: dict[str, Any]) -> None:
    """Seed one isolated worker profile from the already-authenticated primary lane.

    No browser/profile files are copied while Edge is running. Authentication is
    transferred through the browser APIs, avoiding locked SQLite/profile files and
    keeping each worker's persistent profile independent afterwards.
    """

    with sync_playwright() as playwright:
        _browser, context = _connect_browser(playwright, lane.cdp_port)
        cookies = [item for item in state.get("cookies") or [] if isinstance(item, dict)]
        if cookies:
            context.add_cookies(cookies)

        page = next(
            (
                candidate
                for candidate in reversed(list(context.pages))
                if _MAKRO_HOST in str(candidate.url or "").casefold()
            ),
            None,
        )
        if page is None:
            page = context.new_page()
        try:
            if _MAKRO_HOST not in str(page.url or "").casefold():
                page.goto(_MAKRO_ORIGIN, wait_until="domcontentloaded", timeout=25_000)
            local_storage = [
                item
                for item in state.get("local_storage") or []
                if isinstance(item, dict) and str(item.get("name") or "")
            ]
            if local_storage:
                page.evaluate(
                    """items => {
                        for (const item of items) {
                            localStorage.setItem(String(item.name), String(item.value ?? ''));
                        }
                    }""",
                    local_storage,
                )
            page.goto(DEFAULT_START_URL, wait_until="domcontentloaded", timeout=25_000)
        except Exception as exc:
            raise RuntimeError(
                f"{lane.label} 登录会话同步后无法打开 Makro：{exc}"
            ) from exc


def ensure_batch_browser_lanes(
    project_root: str | Path,
    *,
    base_port: int,
    count: int,
) -> tuple[BatchBrowserLane, ...]:
    """Start isolated Edge lanes and seed them from the primary Makro login.

    The primary lane must already be running. Additional Edge processes are
    launched concurrently. Their Makro cookies/localStorage are then synchronized
    from the primary browser through CDP; profile files are never copied.
    """

    lanes = build_batch_browser_lanes(
        project_root,
        base_port=int(base_port),
        count=int(count),
    )
    primary = lanes[0]
    if not is_cdp_ready(primary.cdp_port, timeout_s=1.0):
        raise RuntimeError(
            f"主 Makro Browser CDP {primary.cdp_port} 未就绪，不能建立并行 worker pool。"
        )

    workers = lanes[1:]

    def ensure_lane(lane: BatchBrowserLane) -> None:
        if is_cdp_ready(lane.cdp_port, timeout_s=0.5):
            return
        lane.profile_dir.mkdir(parents=True, exist_ok=True)
        launch_detached_edge(
            profile_dir=lane.profile_dir,
            port=lane.cdp_port,
            start_url=DEFAULT_START_URL,
        )

    if workers:
        with ThreadPoolExecutor(max_workers=len(workers)) as executor:
            list(executor.map(ensure_lane, workers))

        state = _export_primary_login_state(primary.cdp_port)
        with ThreadPoolExecutor(max_workers=len(workers)) as executor:
            futures = [executor.submit(_import_primary_login_state, lane, state) for lane in workers]
            for future in futures:
                future.result()

    missing = [lane.cdp_port for lane in lanes if not browser_lane_token(lane.cdp_port)]
    if missing:
        raise RuntimeError(f"Makro 并行 browser lane 未就绪：ports={missing}")
    return lanes


def lane_tokens(lanes: tuple[BatchBrowserLane, ...]) -> dict[int, str]:
    return {lane.cdp_port: browser_lane_token(lane.cdp_port) for lane in lanes}


__all__ = [
    "BatchBrowserLane",
    "browser_lane_token",
    "build_batch_browser_lanes",
    "ensure_batch_browser_lanes",
    "lane_tokens",
]
