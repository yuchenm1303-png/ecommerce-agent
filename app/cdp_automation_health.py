from __future__ import annotations

import json
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from .browser_session import cdp_attach_guard, cdp_endpoint
from .cdp_transport_lane import exclusive_cdp_transport_lane


ENDPOINT_DOWN = "ENDPOINT_DOWN"
ENDPOINT_ALIVE = "ENDPOINT_ALIVE"
AUTOMATION_READY = "AUTOMATION_READY"
POISONED = "POISONED"
RECOVERING = "RECOVERING"


@dataclass(frozen=True, slots=True)
class CdpAutomationProbe:
    """One bounded observation of a CDP browser generation.

    ``webSocketDebuggerUrl`` proves only that Chromium's DevTools HTTP endpoint
    is alive. ``AUTOMATION_READY`` additionally requires Playwright to finish a
    real CDP attach and expose the browser context/page inventory. The probe never
    closes the external browser; leaving ``sync_playwright`` only drops this
    temporary transport.
    """

    state: str
    endpoint_token: str = ""
    error: str = ""
    context_count: int = 0
    page_count: int = 0

    @property
    def endpoint_alive(self) -> bool:
        return bool(self.endpoint_token)

    @property
    def automation_ready(self) -> bool:
        return self.state == AUTOMATION_READY


def cdp_endpoint_token(port: int, *, timeout_s: float = 0.6) -> str:
    try:
        with urllib.request.urlopen(
            f"{cdp_endpoint(int(port))}/json/version",
            timeout=max(0.1, float(timeout_s)),
        ) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return str(payload.get("webSocketDebuggerUrl") or "").strip()
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return ""


def _compact_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    if len(text) > 500:
        text = text[:497] + "..."
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def probe_cdp_automation(
    port: int,
    *,
    timeout_ms: int = 6_000,
    transport_lane_owned: bool = False,
) -> CdpAutomationProbe:
    """Distinguish endpoint reachability from usable Playwright automation.

    A health probe is itself a real Playwright transport, so it obeys the same
    exclusive transport ownership contract as business automation. Normal GUI
    idle probes acquire the lane here. A recovery flow that already owns the lane
    must pass ``transport_lane_owned=True`` to avoid reacquiring its own lock.
    """

    port = int(port)
    token = cdp_endpoint_token(port)
    if not token:
        return CdpAutomationProbe(state=ENDPOINT_DOWN)

    lane = nullcontext() if transport_lane_owned else exclusive_cdp_transport_lane(port)
    try:
        guard_timeout = max(3.0, (max(1_000, int(timeout_ms)) / 1000.0) + 2.0)
        with lane:
            with cdp_attach_guard(port, timeout_s=guard_timeout):
                with sync_playwright() as playwright:
                    browser = playwright.chromium.connect_over_cdp(
                        cdp_endpoint(port),
                        timeout=max(1_000, int(timeout_ms)),
                    )
                    contexts = list(browser.contexts)
                    if not contexts:
                        raise RuntimeError("CDP attach completed without a browser context")
                    page_count = sum(len(list(context.pages)) for context in contexts)
                    is_connected = getattr(browser, "is_connected", None)
                    if callable(is_connected) and not bool(is_connected()):
                        raise RuntimeError("Playwright transport disconnected during automation probe")
                    return CdpAutomationProbe(
                        state=AUTOMATION_READY,
                        endpoint_token=token,
                        context_count=len(contexts),
                        page_count=page_count,
                    )
    except Exception as exc:
        current = cdp_endpoint_token(port)
        if not current:
            return CdpAutomationProbe(
                state=ENDPOINT_DOWN,
                endpoint_token="",
                error=_compact_error(exc),
            )
        return CdpAutomationProbe(
            state=POISONED,
            endpoint_token=current,
            error=_compact_error(exc),
        )


def _poison_path(port: int) -> Path:
    root = Path(tempfile.gettempdir()) / "ecommerce-agent-cdp"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"poison-{int(port)}.json"


def read_cdp_poison(port: int) -> dict[str, Any]:
    try:
        payload = json.loads(_poison_path(port).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def mark_cdp_poisoned(
    port: int,
    *,
    endpoint_token: str = "",
    reason: str = "",
) -> dict[str, Any]:
    token = str(endpoint_token or cdp_endpoint_token(port)).strip()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "port": int(port),
        "state": POISONED,
        "endpoint_token": token,
        "reason": " ".join(str(reason or "").split())[:1000],
        "marked_unix": time.time(),
    }
    _poison_path(port).write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return payload


def clear_cdp_poison(port: int) -> None:
    try:
        _poison_path(port).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def poison_matches_current_generation(
    port: int,
    endpoint_token: str | None = None,
) -> bool:
    payload = read_cdp_poison(port)
    if not payload:
        return False
    current = str(endpoint_token if endpoint_token is not None else cdp_endpoint_token(port)).strip()
    marked = str(payload.get("endpoint_token") or "").strip()
    if not current:
        return False
    if marked and marked != current:
        clear_cdp_poison(port)
        return False
    return str(payload.get("state") or "").upper() == POISONED


def looks_like_cdp_transport_failure(value: BaseException | str) -> bool:
    """Return True only when Chromium automation is broken, not ownership misuse.

    A transport-lane/parent-owner rejection proves the safety contract worked; it
    says nothing about Chromium health and must never trigger POISONED recovery.
    """

    text = str(value or "").casefold()
    ownership_markers = (
        "禁止嵌套",
        "transport lane",
        "已由当前进程树中的父级 playwright transport 控制",
        "禁止在其仍存活时再次创建独立 edgeharness/connect_over_cdp",
    )
    if any(marker in text for marker in ownership_markers):
        return False

    strong_markers = (
        "playwright attach 连续失败",
        "connect_over_cdp",
        "cdp handshake",
        "ws connected",
    )
    if any(marker in text for marker in strong_markers):
        return True
    return "cdp" in text and any(
        marker in text
        for marker in (
            "attach",
            "timeout",
            "timed out",
            "endpoint not ready",
            "当前不可达",
            "仍可达",
        )
    )


__all__ = [
    "AUTOMATION_READY",
    "CdpAutomationProbe",
    "ENDPOINT_ALIVE",
    "ENDPOINT_DOWN",
    "POISONED",
    "RECOVERING",
    "cdp_endpoint_token",
    "clear_cdp_poison",
    "looks_like_cdp_transport_failure",
    "mark_cdp_poisoned",
    "poison_matches_current_generation",
    "probe_cdp_automation",
    "read_cdp_poison",
]
