from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from .browser_visual_hud import arm_browser_visual_hud


DEFAULT_CDP_PORT = 9222
DEFAULT_START_URL = "https://seller.makro.co.za/"
_MAKRO_HUD_HOST = "seller.makro.co.za"


@dataclass
class SingleEdgeSession:
    """Connection to the one long-lived Makro Edge instance.

    The Edge process is launched independently from Playwright and exposes a
    localhost-only CDP endpoint. Scripts attach/detach from it; they do not own
    the browser lifetime and therefore must not call browser.close() or
    context.close().
    """

    browser: Browser
    context: BrowserContext
    page: Page
    launched_now: bool
    cdp_port: int
    profile_dir: Path


def cdp_endpoint(port: int = DEFAULT_CDP_PORT) -> str:
    return f"http://127.0.0.1:{int(port)}"


def _json_version_url(port: int) -> str:
    return f"{cdp_endpoint(port)}/json/version"


def is_cdp_ready(port: int = DEFAULT_CDP_PORT, *, timeout_s: float = 0.4) -> bool:
    try:
        with urllib.request.urlopen(_json_version_url(port), timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return bool(payload.get("webSocketDebuggerUrl"))
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return False


def _edge_candidates() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(env_name)
        if root:
            candidates.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    on_path = shutil.which("msedge") or shutil.which("msedge.exe")
    if on_path:
        candidates.append(Path(on_path))
    return candidates


def find_edge_executable() -> Path:
    for candidate in _edge_candidates():
        if candidate.exists():
            return candidate
    raise RuntimeError("找不到 Microsoft Edge 可执行文件（msedge.exe）。")


def build_edge_command(
    *,
    executable: Path,
    profile_dir: Path,
    port: int,
    start_url: str = DEFAULT_START_URL,
) -> list[str]:
    return [
        str(executable),
        f"--remote-debugging-port={int(port)}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir.resolve()}",
        "--no-first-run",
        "--no-default-browser-check",
        start_url,
    ]


def launch_detached_edge(
    *,
    profile_dir: Path,
    port: int = DEFAULT_CDP_PORT,
    start_url: str = DEFAULT_START_URL,
    startup_timeout_s: float = 15.0,
) -> None:
    """Launch the dedicated Edge independently so later scripts can reconnect.

    The process intentionally outlives the Python caller. Authentication remains
    inside the dedicated Chromium profile; no cookie/token/session value is read
    or persisted by this helper.
    """

    profile_dir = profile_dir.resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    executable = find_edge_executable()
    command = build_edge_command(
        executable=executable,
        profile_dir=profile_dir,
        port=port,
        start_url=start_url,
    )

    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )

    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )

    deadline = time.monotonic() + startup_timeout_s
    while time.monotonic() < deadline:
        if is_cdp_ready(port):
            return
        time.sleep(0.2)
    raise RuntimeError(
        f"Edge 已尝试启动，但本地 CDP 端口 {port} 未就绪。请确认该端口未被其他程序占用。"
    )


def select_listing_page(context: BrowserContext) -> Page:
    pages = list(context.pages)
    if not pages:
        return context.new_page()

    # Prefer an already-open Makro listing, then any Makro seller tab, then the
    # most recently created tab. We never navigate away from a valid current tab.
    for page in reversed(pages):
        if "seller.makro.co.za" in (page.url or "") and "addListings/single" in (page.url or ""):
            return page
    for page in reversed(pages):
        if "seller.makro.co.za" in (page.url or ""):
            return page
    return pages[-1]


# Backward-compatible alias used by earlier tests/scripts.
_choose_page = select_listing_page


class EdgeHarness:
    """Browser-Harness-style session abstraction for the long-lived Makro Edge.

    Responsibilities:

    - attach to the one long-lived Edge over localhost CDP (launching it only
      when no CDP endpoint exists yet);
    - never own/close the external Edge process (``detach`` is a no-op by
      design; the Edge is launched detached and outlives every script);
    - deterministic page selection (prefer an open listing, then any Makro
      tab, then the most recently created tab);
    - health check and reconnect helpers for long-running sessions;
    - keep the existing Visual Agent HUD attached to Makro automation pages,
      including new tabs and later navigations.

    The harness never reads or logs cookies, tokens, sessionStorage or
    Authorization data. The HUD is display-only and never changes page
    selection, click/fill decisions or browser safety rules.
    """

    def __init__(
        self,
        playwright: Playwright,
        *,
        profile_dir: Path,
        port: int = DEFAULT_CDP_PORT,
        start_url: str = DEFAULT_START_URL,
    ) -> None:
        self.playwright = playwright
        self.profile_dir = Path(profile_dir).resolve()
        self.cdp_port = int(port)
        self.launched_now = not is_cdp_ready(self.cdp_port)
        if self.launched_now:
            launch_detached_edge(
                profile_dir=self.profile_dir, port=self.cdp_port, start_url=start_url
            )
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._watched_page_ids: set[int] = set()
        self._watched_context_ids: set[int] = set()
        self._connect()

    def _watch_visual_page(self, page: Page) -> None:
        """Arm one domain-scoped HUD lifecycle; the facade owns navigation."""

        if page.is_closed():
            return
        key = id(page)
        self._watched_page_ids.add(key)
        arm_browser_visual_hud(
            page,
            title="Makro 浏览器自动化运行中",
            thought="Listing Studio 正在读取、检索或操作当前 Makro 页面。",
            phase=1,
            host_suffix=_MAKRO_HUD_HOST,
        )

    def _watch_visual_context(self, context: BrowserContext) -> None:
        key = id(context)
        if key in self._watched_context_ids:
            return
        self._watched_context_ids.add(key)
        try:
            context.on("page", self._watch_visual_page)
        except Exception:
            pass
        for page in list(context.pages):
            self._watch_visual_page(page)

    def _connect(self) -> None:
        browser = self.playwright.chromium.connect_over_cdp(cdp_endpoint(self.cdp_port))
        contexts = list(browser.contexts)
        if not contexts:
            raise RuntimeError("已连接 Edge，但没有可用 browser context。")
        self.browser = browser
        self.context = contexts[0]
        self._watch_visual_context(self.context)
        self.page = select_listing_page(self.context)
        self._watch_visual_page(self.page)

    def health_check(self) -> bool:
        """True when the long-lived Edge still exposes its CDP endpoint."""
        return is_cdp_ready(self.cdp_port)

    def select_page(self) -> Page:
        """Deterministically re-select the best page in the current context."""
        if self.context is None:
            raise RuntimeError("Edge harness 尚未连接 context。")
        self.page = select_listing_page(self.context)
        self._watch_visual_page(self.page)
        return self.page

    def ensure_page(self) -> Page:
        """Return the current page, re-attaching when it was closed/detached."""
        if not self.health_check():
            raise RuntimeError("长期 Makro Edge 的 CDP 端点不可达，无法继续。")
        if self.page is None or self.page.is_closed():
            self._connect()
        assert self.page is not None
        self._watch_visual_page(self.page)
        return self.page

    def detach(self) -> None:
        """Drop our connection without closing the external Edge process.

        The Edge is launched independently (launch_detached_edge) and is
        intentionally never closed by scripts; leaving the Playwright
        connection is enough for later runs to re-attach over CDP.
        """
        self.page = None
        self.context = None
        self.browser = None


def connect_single_edge(
    playwright: Playwright,
    *,
    profile_dir: Path,
    port: int = DEFAULT_CDP_PORT,
    start_url: str = DEFAULT_START_URL,
) -> SingleEdgeSession:
    """Backward-compatible wrapper around :class:`EdgeHarness`.

    Returns a :class:`SingleEdgeSession` exposing the same fields as before.
    Callers must simply let their Playwright connection end; the harness never
    closes the external Edge.
    """

    harness = EdgeHarness(
        playwright,
        profile_dir=profile_dir,
        port=port,
        start_url=start_url,
    )
    return SingleEdgeSession(
        browser=harness.browser,
        context=harness.context,
        page=harness.page,
        launched_now=harness.launched_now,
        cdp_port=harness.cdp_port,
        profile_dir=harness.profile_dir,
    )
