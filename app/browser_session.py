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
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, Playwright


DEFAULT_CDP_PORT = 9222
DEFAULT_START_URL = "https://seller.makro.co.za/"


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


def _choose_page(context: BrowserContext) -> Page:
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


def connect_single_edge(
    playwright: Playwright,
    *,
    profile_dir: Path,
    port: int = DEFAULT_CDP_PORT,
    start_url: str = DEFAULT_START_URL,
) -> SingleEdgeSession:
    """Attach to the existing Makro Edge, launching it only if none exists.

    Once the detached Edge has been launched, later invocations reconnect to the
    same browser/profile/login session through localhost CDP. Callers must simply
    let their Playwright connection end; do not close the browser/context.
    """

    launched_now = False
    if not is_cdp_ready(port):
        launch_detached_edge(profile_dir=profile_dir, port=port, start_url=start_url)
        launched_now = True

    browser = playwright.chromium.connect_over_cdp(cdp_endpoint(port))
    contexts = list(browser.contexts)
    if not contexts:
        raise RuntimeError("已连接 Edge，但没有可用 browser context。")
    context = contexts[0]
    page = _choose_page(context)
    return SingleEdgeSession(
        browser=browser,
        context=context,
        page=page,
        launched_now=launched_now,
        cdp_port=port,
        profile_dir=profile_dir.resolve(),
    )
