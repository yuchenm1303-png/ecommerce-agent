"""Close only Listing Studio's managed Edge before an in-place Windows upgrade.

The formal GUI owns one dedicated Microsoft Edge instance through the reserved
localhost CDP port 9222.  Update shutdown must never kill arbitrary user Edge
windows by image name; ownership is proven by the TCP listener PID and then by
that PID's exact ``msedge.exe`` image identity.
"""

from __future__ import annotations

import csv
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

DEFAULT_CDP_PORT = 9222
OWNED_BROWSER_IMAGE = "msedge.exe"
_CREATE_NO_WINDOW = 0x08000000
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class BrowserCloseResult:
    ok: bool
    detail: str = ""
    pid: int = 0


def _log(path: str | Path | None, message: str) -> None:
    if not path:
        return
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with target.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}\t{message}\n")
    except OSError:
        pass


def _endpoint_port(value: str) -> int:
    text = str(value or "").strip()
    if ":" not in text:
        return 0
    try:
        return int(text.rsplit(":", 1)[1])
    except ValueError:
        return 0


def listener_pid(port: int) -> int:
    """Return the PID listening on the reserved local CDP port, or 0."""

    wanted = int(port)
    if wanted <= 0 or os.name != "nt":
        return 0
    try:
        probe = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return 0

    for raw in (probe.stdout or "").splitlines():
        parts = raw.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if _endpoint_port(parts[1]) != wanted:
            continue
        host = parts[1].rsplit(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "0.0.0.0", "::1", "::"}:
            continue
        if parts[-2].upper() != "LISTENING":
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 0:
            return pid
    return 0


def _pid_image_name(pid: int) -> str:
    if int(pid) <= 0 or os.name != "nt":
        return ""
    try:
        probe = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    try:
        for row in csv.reader((probe.stdout or "").splitlines()):
            if len(row) < 2:
                continue
            try:
                process_id = int(str(row[1]).replace(",", "").strip())
            except ValueError:
                continue
            if process_id == int(pid):
                return str(row[0] or "").strip().lower()
    except csv.Error:
        pass
    return ""


def _wait_listener_closed(port: int, deadline_s: float) -> bool:
    deadline = time.monotonic() + max(0.0, float(deadline_s))
    while time.monotonic() < deadline:
        if listener_pid(port) <= 0:
            return True
        time.sleep(0.15)
    return listener_pid(port) <= 0


def close_managed_browser(
    *,
    port: int = DEFAULT_CDP_PORT,
    deadline_s: float = 6.0,
    log_path: str | Path | None = None,
    progress: ProgressCallback | None = None,
) -> BrowserCloseResult:
    """Close the one managed Edge process tree and nothing else.

    No listener means there is nothing to close. If another image owns the CDP
    port, fail closed and leave it untouched. This keeps normal user Edge windows
    out of updater process management.
    """

    pid = listener_pid(port)
    if pid <= 0:
        _log(log_path, f"browser gate: no managed Edge listener on CDP {port}")
        return BrowserCloseResult(True)

    image = _pid_image_name(pid)
    if image != OWNED_BROWSER_IMAGE:
        detail = (
            f"CDP port {port} is owned by unexpected process "
            f"{image or 'unknown'} pid={pid}; refusing to terminate it"
        )
        _log(log_path, f"browser gate failed: {detail}")
        return BrowserCloseResult(False, detail, pid)

    if progress is not None:
        try:
            progress("正在关闭 Makro Browser，释放更新文件…")
        except Exception:
            pass
    _log(log_path, f"browser gate closing managed Edge pid={pid} cdp_port={port}")
    try:
        probe = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = f"failed to terminate managed Edge pid={pid}: {exc}"
        _log(log_path, f"browser gate failed: {detail}")
        return BrowserCloseResult(False, detail, pid)

    if probe.returncode != 0 and listener_pid(port) > 0:
        detail = f"taskkill failed for managed Edge pid={pid} exit={probe.returncode}"
        _log(log_path, f"browser gate failed: {detail}")
        return BrowserCloseResult(False, detail, pid)
    if not _wait_listener_closed(port, deadline_s):
        detail = f"managed Edge CDP port {port} remained open after termination"
        _log(log_path, f"browser gate failed: {detail}")
        return BrowserCloseResult(False, detail, pid)

    _log(log_path, f"browser gate closed managed Edge pid={pid} cdp_port={port}")
    return BrowserCloseResult(True, pid=pid)


__all__ = [
    "BrowserCloseResult",
    "DEFAULT_CDP_PORT",
    "OWNED_BROWSER_IMAGE",
    "close_managed_browser",
    "listener_pid",
]
