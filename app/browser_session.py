from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from .browser_visual_hud import arm_browser_visual_hud


DEFAULT_CDP_PORT = 9222
DEFAULT_START_URL = "https://seller.makro.co.za/"
_MAKRO_HUD_HOST = "seller.makro.co.za"
_CDP_ATTACH_ATTEMPTS = 3
_CDP_ATTACH_TIMEOUT_MS = 25_000
_CDP_ATTACH_LOCK_TIMEOUT_S = 90.0
_CDP_ATTACH_RETRY_DELAY_S = 0.75
_CDP_ATTACH_LOCK_POLL_S = 0.10
_EXTERNAL_SPAWN_LOCK = threading.Lock()


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


def _path_is_inside(candidate: str, root: str) -> bool:
    if not candidate or not root:
        return False
    try:
        candidate_abs = os.path.normcase(os.path.abspath(candidate))
        root_abs = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([candidate_abs, root_abs]) == root_abs
    except (OSError, ValueError):
        return False


def fresh_external_child_environment() -> dict[str, str]:
    """Return an environment that does not leak PyInstaller runtime state."""

    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("_PYI_"):
            env.pop(key, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    bundle_root = str(getattr(sys, "_MEIPASS", "") or "")
    if bundle_root:
        entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
        env["PATH"] = os.pathsep.join(
            entry for entry in entries if not _path_is_inside(entry, bundle_root)
        )
    return env


def _spawn_external(command: list[str], *, creationflags: int) -> subprocess.Popen[bytes]:
    """Spawn a real external program without inheriting frozen DLL search state."""

    env = fresh_external_child_environment()
    bundle_root = str(getattr(sys, "_MEIPASS", "") or "")
    with _EXTERNAL_SPAWN_LOCK:
        reset_dll_directory = os.name == "nt" and bool(bundle_root)
        if reset_dll_directory:
            try:
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            except (AttributeError, OSError):
                reset_dll_directory = False
        try:
            return subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
                env=env,
            )
        finally:
            if reset_dll_directory:
                try:
                    ctypes.windll.kernel32.SetDllDirectoryW(bundle_root)
                except (AttributeError, OSError):
                    pass


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
) -> int:
    """Launch the dedicated Edge independently with a clean external runtime."""

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

    proc = _spawn_external(command, creationflags=creationflags)
    deadline = time.monotonic() + startup_timeout_s
    while time.monotonic() < deadline:
        if is_cdp_ready(port):
            return int(proc.pid)
        time.sleep(0.2)
    raise RuntimeError(
        f"Edge 已尝试启动，但本地 CDP 端口 {port} 未就绪。请确认该端口未被其他程序占用。"
    )


def _cdp_attach_lock_path(port: int) -> Path:
    root = Path(tempfile.gettempdir()) / "ecommerce-agent-cdp"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"attach-{int(port)}.lock"


def _try_lock_handle(handle: object) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            handle.seek(0)  # type: ignore[attr-defined]
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def _unlock_handle(handle: object) -> None:
    if os.name == "nt":
        import msvcrt

        try:
            handle.seek(0)  # type: ignore[attr-defined]
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        except OSError:
            pass
        return

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
    except OSError:
        pass


@contextmanager
def cdp_attach_guard(
    port: int = DEFAULT_CDP_PORT,
    *,
    timeout_s: float = _CDP_ATTACH_LOCK_TIMEOUT_S,
) -> Iterator[None]:
    """Serialize Playwright CDP handshakes across all local worker processes."""

    lock_path = _cdp_attach_lock_path(port)
    deadline = time.monotonic() + max(1.0, float(timeout_s))
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        acquired = False
        while time.monotonic() < deadline:
            if _try_lock_handle(handle):
                acquired = True
                break
            time.sleep(_CDP_ATTACH_LOCK_POLL_S)
        if not acquired:
            raise RuntimeError(
                f"等待 Makro Edge CDP {int(port)} attach 锁超时；"
                "已有其他任务正在建立浏览器控制连接。"
            )
        try:
            yield
        finally:
            _unlock_handle(handle)


def _format_attach_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    if len(text) > 260:
        text = text[:257] + "..."
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _connect_browser_resilient(playwright: Playwright, port: int) -> Browser:
    endpoint = cdp_endpoint(port)
    failures: list[str] = []

    with cdp_attach_guard(port):
        for attempt in range(1, _CDP_ATTACH_ATTEMPTS + 1):
            if not is_cdp_ready(port, timeout_s=1.0):
                failures.append(f"attempt {attempt}: CDP endpoint not ready")
            else:
                try:
                    browser = playwright.chromium.connect_over_cdp(
                        endpoint,
                        timeout=_CDP_ATTACH_TIMEOUT_MS,
                    )
                    if not list(browser.contexts):
                        raise RuntimeError("已连接 Edge，但没有可用 browser context。")
                    if attempt > 1:
                        print(
                            f"CDP_ATTACH RECOVERED port={int(port)} attempt={attempt}",
                            flush=True,
                        )
                    return browser
                except Exception as exc:
                    failures.append(f"attempt {attempt}: {_format_attach_error(exc)}")

            if attempt < _CDP_ATTACH_ATTEMPTS:
                print(
                    f"CDP_ATTACH RETRY port={int(port)} attempt={attempt}/{_CDP_ATTACH_ATTEMPTS}",
                    flush=True,
                )
                time.sleep(_CDP_ATTACH_RETRY_DELAY_S)

    detail = " | ".join(failures[-_CDP_ATTACH_ATTEMPTS:])
    ready = is_cdp_ready(port, timeout_s=1.0)
    raise RuntimeError(
        f"长期 Makro Edge CDP {endpoint} "
        + ("仍可达，但 Playwright attach 连续失败。" if ready else "当前不可达。")
        + " 不会自动关闭或重启 Edge。"
        + (f" diagnostics={detail}" if detail else "")
    )


def select_listing_page(context: BrowserContext) -> Page:
    pages = list(context.pages)
    if not pages:
        return context.new_page()
    for page in reversed(pages):
        if "seller.makro.co.za" in (page.url or "") and "addListings/single" in (page.url or ""):
            return page
    for page in reversed(pages):
        if "seller.makro.co.za" in (page.url or ""):
            return page
    return pages[-1]


_choose_page = select_listing_page


class EdgeHarness:
    """Browser-Harness-style session abstraction for the long-lived Makro Edge."""

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
        browser = _connect_browser_resilient(self.playwright, self.cdp_port)
        contexts = list(browser.contexts)
        if not contexts:
            raise RuntimeError("已连接 Edge，但没有可用 browser context。")
        self.browser = browser
        self.context = contexts[0]
        self._watch_visual_context(self.context)
        self.page = select_listing_page(self.context)
        self._watch_visual_page(self.page)

    def health_check(self) -> bool:
        return is_cdp_ready(self.cdp_port)

    def select_page(self) -> Page:
        if self.context is None:
            raise RuntimeError("Edge harness 尚未连接 context。")
        self.page = select_listing_page(self.context)
        self._watch_visual_page(self.page)
        return self.page

    def ensure_page(self) -> Page:
        if not self.health_check():
            raise RuntimeError("长期 Makro Edge 的 CDP 端点不可达，无法继续。")
        if self.page is None or self.page.is_closed():
            self._connect()
        assert self.page is not None
        self._watch_visual_page(self.page)
        return self.page

    def detach(self) -> None:
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
