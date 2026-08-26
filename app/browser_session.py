from __future__ import annotations

import ctypes
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
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
_CDP_SESSION_WAIT_LOG_S = 5.0
_CDP_SESSION_ENV_PREFIX = "ECOMMERCE_CDP_SESSION_LEASE_"
_EXTERNAL_SPAWN_LOCK = threading.Lock()
_CDP_SESSION_LOCAL_LOCKS_GUARD = threading.Lock()
_CDP_SESSION_LOCAL_LOCKS: dict[int, threading.RLock] = {}


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
    _session_lease: CdpSessionLease | None = field(default=None, repr=False)

    def detach(self) -> None:
        lease = self._session_lease
        self._session_lease = None
        if lease is not None:
            lease.release()


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


def _cdp_lock_root() -> Path:
    root = Path(tempfile.gettempdir()) / "ecommerce-agent-cdp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cdp_attach_lock_path(port: int) -> Path:
    return _cdp_lock_root() / f"attach-{int(port)}.lock"


def _cdp_session_lock_path(port: int) -> Path:
    return _cdp_lock_root() / f"session-{int(port)}.lock"


def _cdp_session_owner_path(port: int) -> Path:
    return _cdp_lock_root() / f"session-{int(port)}.owner.json"


def _cdp_session_env_key(port: int) -> str:
    return f"{_CDP_SESSION_ENV_PREFIX}{int(port)}"


def _cdp_session_local_lock(port: int) -> threading.RLock:
    key = int(port)
    with _CDP_SESSION_LOCAL_LOCKS_GUARD:
        lock = _CDP_SESSION_LOCAL_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _CDP_SESSION_LOCAL_LOCKS[key] = lock
        return lock


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


def _read_cdp_session_owner(port: int) -> dict[str, object]:
    path = _cdp_session_owner_path(port)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_cdp_session_owner(port: int, *, token: str) -> None:
    path = _cdp_session_owner_path(port)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "port": int(port),
                "token": token,
                "owner_pid": os.getpid(),
                "acquired_unix": time.time(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


class CdpSessionLease:
    """One exclusive long-lived Edge automation owner, inheritable by child workers."""

    def __init__(
        self,
        *,
        port: int,
        token: str,
        local_lock: threading.RLock,
        handle: object | None,
        inherited: bool,
        previous_env: str | None,
    ) -> None:
        self.port = int(port)
        self.token = token
        self._local_lock = local_lock
        self._handle = handle
        self.inherited = bool(inherited)
        self._previous_env = previous_env
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            if not self.inherited:
                owner = _read_cdp_session_owner(self.port)
                if str(owner.get("token") or "") == self.token:
                    try:
                        _cdp_session_owner_path(self.port).unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        pass

                env_key = _cdp_session_env_key(self.port)
                if os.environ.get(env_key) == self.token:
                    if self._previous_env is None:
                        os.environ.pop(env_key, None)
                    else:
                        os.environ[env_key] = self._previous_env

                if self._handle is not None:
                    _unlock_handle(self._handle)
                    try:
                        self._handle.close()  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    self._handle = None
                print(
                    f"CDP_SESSION RELEASED port={self.port} owner_pid={os.getpid()}",
                    flush=True,
                )
        finally:
            self._local_lock.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass


def acquire_cdp_session_lease(port: int = DEFAULT_CDP_PORT) -> CdpSessionLease:
    """Queue for exclusive control of one long-lived CDP browser session.

    The lease spans the whole EdgeHarness lifetime, not only connect_over_cdp().
    Synchronous child processes inherit a cryptographic token and may re-enter the
    same owner's lease; unrelated jobs must wait until the owner detaches. The OS
    file lock is released automatically if the owning process crashes.
    """

    port = int(port)
    local_lock = _cdp_session_local_lock(port)
    local_lock.acquire()
    handle: object | None = None
    acquired_external = False
    try:
        env_key = _cdp_session_env_key(port)
        inherited_token = str(os.environ.get(env_key) or "").strip()
        if inherited_token:
            owner = _read_cdp_session_owner(port)
            if str(owner.get("token") or "") == inherited_token:
                print(
                    f"CDP_SESSION INHERITED port={port} owner_pid={owner.get('owner_pid', '')} child_pid={os.getpid()}",
                    flush=True,
                )
                return CdpSessionLease(
                    port=port,
                    token=inherited_token,
                    local_lock=local_lock,
                    handle=None,
                    inherited=True,
                    previous_env=None,
                )
            os.environ.pop(env_key, None)

        lock_path = _cdp_session_lock_path(port)
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)  # type: ignore[attr-defined]
        if handle.tell() == 0:  # type: ignore[attr-defined]
            handle.write(b"0")  # type: ignore[attr-defined]
            handle.flush()  # type: ignore[attr-defined]

        next_wait_log = time.monotonic() + _CDP_SESSION_WAIT_LOG_S
        while not _try_lock_handle(handle):
            now = time.monotonic()
            if now >= next_wait_log:
                owner = _read_cdp_session_owner(port)
                print(
                    "CDP_SESSION WAIT "
                    f"port={port} owner_pid={owner.get('owner_pid', '<unknown>')}",
                    flush=True,
                )
                next_wait_log = now + _CDP_SESSION_WAIT_LOG_S
            time.sleep(_CDP_ATTACH_LOCK_POLL_S)
        acquired_external = True

        token = secrets.token_hex(16)
        previous_env = os.environ.get(env_key)
        os.environ[env_key] = token
        try:
            _write_cdp_session_owner(port, token=token)
        except Exception:
            if previous_env is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = previous_env
            raise

        print(
            f"CDP_SESSION ACQUIRED port={port} owner_pid={os.getpid()}",
            flush=True,
        )
        return CdpSessionLease(
            port=port,
            token=token,
            local_lock=local_lock,
            handle=handle,
            inherited=False,
            previous_env=previous_env,
        )
    except Exception:
        if acquired_external and handle is not None:
            _unlock_handle(handle)
        if handle is not None:
            try:
                handle.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        local_lock.release()
        raise


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
    """Exclusive session abstraction for the one long-lived Makro Edge."""

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
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._watched_page_ids: set[int] = set()
        self._watched_context_ids: set[int] = set()
        self._session_lease: CdpSessionLease | None = None
        try:
            self._session_lease = acquire_cdp_session_lease(self.cdp_port)
            self.launched_now = not is_cdp_ready(self.cdp_port)
            if self.launched_now:
                launch_detached_edge(
                    profile_dir=self.profile_dir, port=self.cdp_port, start_url=start_url
                )
            self._connect()
        except Exception:
            self._release_session_lease()
            raise

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

    def _release_session_lease(self) -> None:
        lease = self._session_lease
        self._session_lease = None
        if lease is not None:
            lease.release()

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
        self._release_session_lease()

    def __del__(self) -> None:
        try:
            self._release_session_lease()
        except Exception:
            pass


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
    lease = harness._session_lease
    harness._session_lease = None
    return SingleEdgeSession(
        browser=harness.browser,
        context=harness.context,
        page=harness.page,
        launched_now=harness.launched_now,
        cdp_port=harness.cdp_port,
        profile_dir=harness.profile_dir,
        _session_lease=lease,
    )
