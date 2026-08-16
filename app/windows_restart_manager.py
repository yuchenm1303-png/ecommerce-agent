"""Windows Restart Manager audit for updater install-tree file locks.

The standalone updater uses this after all Listing Studio owned processes have
stopped and before Inno starts replacing files. Unknown third-party blockers are
reported before installation instead of being discovered later as an opaque
installer exit code.
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_ERROR_SUCCESS = 0
_ERROR_MORE_DATA = 234
_CCH_RM_SESSION_KEY = 32
_CCH_RM_MAX_APP_NAME = 255
_CCH_RM_MAX_SVC_NAME = 63
_REGISTER_CHUNK = 128


@dataclass(frozen=True)
class LockingProcess:
    pid: int
    app_name: str


@dataclass(frozen=True)
class LockAuditResult:
    ok: bool
    blockers: tuple[LockingProcess, ...] = ()
    error: str = ""


class _RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [
        ("dwProcessId", wintypes.DWORD),
        ("ProcessStartTime", wintypes.FILETIME),
    ]


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", _RM_UNIQUE_PROCESS),
        ("strAppName", wintypes.WCHAR * (_CCH_RM_MAX_APP_NAME + 1)),
        ("strServiceShortName", wintypes.WCHAR * (_CCH_RM_MAX_SVC_NAME + 1)),
        ("ApplicationType", ctypes.c_int),
        ("AppStatus", wintypes.ULONG),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    ]


def _install_tree_resources(root: Path) -> tuple[str, ...]:
    """Return concrete files Inno may replace/delete in the installed tree."""

    root = Path(root).resolve()
    if not root.is_dir():
        return ()
    files: list[str] = []
    try:
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    files.append(str(path.resolve()))
            except OSError:
                continue
    except OSError:
        return ()
    return tuple(files)


def _configure_api(dll: object) -> None:
    dll.RmStartSession.argtypes = [
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
        wintypes.LPWSTR,
    ]
    dll.RmStartSession.restype = wintypes.DWORD
    dll.RmRegisterResources.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.UINT,
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.POINTER(wintypes.LPCWSTR),
    ]
    dll.RmRegisterResources.restype = wintypes.DWORD
    dll.RmGetList.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(_RM_PROCESS_INFO),
        ctypes.POINTER(wintypes.DWORD),
    ]
    dll.RmGetList.restype = wintypes.DWORD
    dll.RmEndSession.argtypes = [wintypes.DWORD]
    dll.RmEndSession.restype = wintypes.DWORD


def _audit_once(root: Path, ignore_pids: set[int]) -> LockAuditResult:
    if os.name != "nt":
        return LockAuditResult(True)
    resources = _install_tree_resources(root)
    if not resources:
        return LockAuditResult(False, error=f"install tree has no auditable files: {root}")

    try:
        dll = ctypes.WinDLL("rstrtmgr.dll")
        _configure_api(dll)
    except (AttributeError, OSError) as exc:
        return LockAuditResult(False, error=f"Restart Manager unavailable: {exc}")

    handle = wintypes.DWORD(0)
    key = ctypes.create_unicode_buffer(_CCH_RM_SESSION_KEY + 1)
    started = False
    try:
        code = int(dll.RmStartSession(ctypes.byref(handle), 0, key))
        if code != _ERROR_SUCCESS:
            return LockAuditResult(False, error=f"RmStartSession failed: {code}")
        started = True

        for index in range(0, len(resources), _REGISTER_CHUNK):
            chunk = resources[index : index + _REGISTER_CHUNK]
            array_type = wintypes.LPCWSTR * len(chunk)
            file_array = array_type(*chunk)
            code = int(
                dll.RmRegisterResources(
                    handle.value,
                    len(chunk),
                    file_array,
                    0,
                    None,
                    0,
                    None,
                )
            )
            if code != _ERROR_SUCCESS:
                return LockAuditResult(False, error=f"RmRegisterResources failed: {code}")

        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reasons = wintypes.DWORD(0)
        code = int(
            dll.RmGetList(
                handle.value,
                ctypes.byref(needed),
                ctypes.byref(count),
                None,
                ctypes.byref(reasons),
            )
        )
        if code == _ERROR_SUCCESS and needed.value == 0:
            return LockAuditResult(True)
        if code != _ERROR_MORE_DATA or needed.value <= 0:
            return LockAuditResult(False, error=f"RmGetList(size) failed: {code}")

        capacity = max(1, int(needed.value))
        buffer_type = _RM_PROCESS_INFO * capacity
        buffer = buffer_type()
        count = wintypes.UINT(capacity)
        code = int(
            dll.RmGetList(
                handle.value,
                ctypes.byref(needed),
                ctypes.byref(count),
                buffer,
                ctypes.byref(reasons),
            )
        )
        if code != _ERROR_SUCCESS:
            return LockAuditResult(False, error=f"RmGetList(data) failed: {code}")

        blockers: dict[int, LockingProcess] = {}
        for item in buffer[: int(count.value)]:
            pid = int(item.Process.dwProcessId)
            if pid <= 0 or pid in ignore_pids:
                continue
            name = str(item.strAppName or "").strip() or f"PID {pid}"
            blockers[pid] = LockingProcess(pid=pid, app_name=name)
        return LockAuditResult(True, tuple(blockers[pid] for pid in sorted(blockers)))
    except Exception as exc:
        return LockAuditResult(False, error=f"Restart Manager audit failed: {exc}")
    finally:
        if started:
            try:
                dll.RmEndSession(handle.value)
            except Exception:
                pass


def audit_install_tree_locks(
    root: str | Path,
    *,
    ignore_pids: Iterable[int] = (),
    attempts: int = 3,
    retry_delay_s: float = 0.35,
) -> LockAuditResult:
    """Audit installed files, retrying briefly to ignore transient scanners."""

    ignored = {int(pid) for pid in ignore_pids if int(pid) > 0}
    last = LockAuditResult(False, error="Restart Manager audit did not run")
    for attempt in range(max(1, int(attempts))):
        last = _audit_once(Path(root), ignored)
        if last.ok and not last.blockers:
            return last
        if attempt + 1 < max(1, int(attempts)):
            time.sleep(max(0.0, float(retry_delay_s)))
    return last


__all__ = [
    "LockAuditResult",
    "LockingProcess",
    "audit_install_tree_locks",
]
