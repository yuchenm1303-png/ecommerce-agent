from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QApplication, QWidget

from .app_access import ApplicationAccessController


_SAMPLE_MS = 60_000
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _filetime_value(value: _FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _windows_memory() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    state = _MEMORYSTATUSEX()
    state.dwLength = ctypes.sizeof(state)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        return {}
    total = int(state.ullTotalPhys)
    available = int(state.ullAvailPhys)
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": max(0, total - available),
        "used_percent": float(state.dwMemoryLoad),
    }


def _process_memory(handle: int | None = None) -> dict[str, int]:
    if os.name != "nt":
        return {}
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    owned = False
    if handle is None:
        handle = kernel32.GetCurrentProcess()
    counters = _PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        if owned:
            kernel32.CloseHandle(handle)
        return {}
    result = {
        "working_set_bytes": int(counters.WorkingSetSize),
        "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
        "pagefile_bytes": int(counters.PagefileUsage),
        "peak_pagefile_bytes": int(counters.PeakPagefileUsage),
        "page_faults": int(counters.PageFaultCount),
    }
    if owned:
        kernel32.CloseHandle(handle)
    return result


def _system_cpu_times() -> tuple[int, int, int] | None:
    if os.name != "nt":
        return None
    idle = _FILETIME()
    kernel = _FILETIME()
    user = _FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return _filetime_value(idle), _filetime_value(kernel), _filetime_value(user)


def _process_cpu_times() -> tuple[int, int] | None:
    if os.name != "nt":
        return None
    creation = _FILETIME()
    exit_time = _FILETIME()
    kernel = _FILETIME()
    user = _FILETIME()
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if not ctypes.windll.kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        return None
    return _filetime_value(kernel), _filetime_value(user)


def _edge_processes() -> dict[str, int]:
    if os.name != "nt":
        return {"count": 0, "working_set_bytes": 0}
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        return {"count": 0, "working_set_bytes": 0}

    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    count = 0
    working_set = 0
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            name = str(entry.szExeFile or "").casefold()
            if name in {"msedge.exe", "msedgewebview2.exe"}:
                count += 1
                handle = kernel32.OpenProcess(
                    _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ,
                    False,
                    int(entry.th32ProcessID),
                )
                if handle:
                    try:
                        working_set += int(_process_memory(handle).get("working_set_bytes", 0))
                    finally:
                        kernel32.CloseHandle(handle)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return {"count": count, "working_set_bytes": working_set}


def _disk_snapshot() -> dict[str, Any]:
    try:
        anchor = Path.home().anchor or str(Path.home())
        usage = shutil.disk_usage(anchor)
    except Exception:
        return {}
    total = int(usage.total)
    free = int(usage.free)
    used = int(usage.used)
    return {
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "used_percent": round((used / total) * 100.0, 2) if total else 0.0,
    }


def _runner_active(value: Any) -> bool:
    candidate = getattr(value, "is_running", False)
    if callable(candidate):
        try:
            return bool(candidate())
        except Exception:
            return False
    return bool(candidate)


class SystemHealthTelemetryController(QObject):
    """Low-frequency operational telemetry for licensed Windows clients.

    Samples only runtime health and coarse task state. It never reads customer
    files, product content, credentials or browser page contents.
    """

    def __init__(self, window: QWidget, access: ApplicationAccessController) -> None:
        super().__init__(window)
        self.window = window
        self.access = access
        self.network = QNetworkAccessManager(self)
        self.timer = QTimer(self)
        self.timer.setInterval(_SAMPLE_MS)
        self.timer.timeout.connect(self._sample)
        self._started = time.monotonic()
        self._expected_tick = self._started + (_SAMPLE_MS / 1000.0)
        self._last_system_cpu = _system_cpu_times()
        self._last_process_cpu = _process_cpu_times()
        self._last_cpu_wall = self._started
        self._last_request_latency_ms = 0.0
        self._last_http_status = 0
        self._pending_requests = 0

        if not self._enabled():
            return
        QTimer.singleShot(8_000, self._sample)
        self.timer.start()

    def _enabled(self) -> bool:
        session = self.access.session
        return bool(session.enforced and session.user_id and session.device_id and session.telemetry_token)

    def _cpu_snapshot(self) -> dict[str, float]:
        now = time.monotonic()
        output = {"system_percent": 0.0, "process_percent": 0.0}

        current_system = _system_cpu_times()
        if current_system and self._last_system_cpu:
            idle_delta = current_system[0] - self._last_system_cpu[0]
            total_delta = (current_system[1] - self._last_system_cpu[1]) + (current_system[2] - self._last_system_cpu[2])
            if total_delta > 0:
                output["system_percent"] = round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 2)
        self._last_system_cpu = current_system

        current_process = _process_cpu_times()
        wall_delta = max(0.001, now - self._last_cpu_wall)
        if current_process and self._last_process_cpu:
            cpu_delta_100ns = (current_process[0] - self._last_process_cpu[0]) + (current_process[1] - self._last_process_cpu[1])
            cpu_seconds = cpu_delta_100ns / 10_000_000.0
            logical = max(1, int(os.cpu_count() or 1))
            output["process_percent"] = round(max(0.0, min(100.0, (cpu_seconds / wall_delta / logical) * 100.0)), 2)
        self._last_process_cpu = current_process
        self._last_cpu_wall = now
        return output

    def _task_state(self) -> dict[str, Any]:
        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        batch = getattr(controller, "batch", None)
        return {
            "single_prepare_running": _runner_active(getattr(self.window, "runner", None)),
            "single_execute_running": _runner_active(getattr(self.window, "execution_runner", None)),
            "batch_running": bool(getattr(controller, "running", False) or _runner_active(controller)),
            "batch_status": str(getattr(batch, "status", "") or "")[:120],
        }

    def _window_state(self, lag_ms: float) -> dict[str, Any]:
        app = QApplication.instance()
        size = self.window.size()
        return {
            "visible": bool(self.window.isVisible()),
            "minimized": bool(self.window.isMinimized()),
            "maximized": bool(self.window.isMaximized()),
            "active": bool(app and app.activeWindow() is self.window),
            "width": int(size.width()),
            "height": int(size.height()),
            "event_loop_lag_ms": round(max(0.0, lag_ms), 2),
        }

    def _payload(self, lag_ms: float) -> dict[str, Any]:
        memory = _windows_memory()
        process_memory = _process_memory()
        edge = _edge_processes()
        return {
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version()[:200],
                "machine": platform.machine(),
                "python": platform.python_version(),
                "logical_cpus": int(os.cpu_count() or 0),
                "frozen": bool(getattr(sys, "frozen", False)),
            },
            "cpu": self._cpu_snapshot(),
            "memory": memory,
            "process": {"pid": os.getpid(), **process_memory},
            "edge": edge,
            "disk": _disk_snapshot(),
            "window": self._window_state(lag_ms),
            "task": self._task_state(),
            "telemetry": {
                "last_request_latency_ms": round(self._last_request_latency_ms, 2),
                "last_http_status": int(self._last_http_status),
                "pending_requests": int(self._pending_requests),
            },
            "uptime_seconds": round(max(0.0, time.monotonic() - self._started), 2),
        }

    def _sample(self) -> None:
        if not self._enabled():
            return
        now = time.monotonic()
        lag_ms = max(0.0, (now - self._expected_tick) * 1000.0)
        self._expected_tick = now + (_SAMPLE_MS / 1000.0)

        session = self.access.session
        payload = {
            "action": "system_sample",
            "user_id": session.user_id,
            "device_id": session.device_id,
            "session_id": getattr(getattr(self.window, "_usage_telemetry", None), "session_id", ""),
            "telemetry_token": session.telemetry_token,
            "app_version": self.access.installed_version,
            "sample": self._payload(lag_ms),
        }
        if not payload["session_id"]:
            return

        request = QNetworkRequest(QUrl(self.access.telemetry_function_url))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        started = time.monotonic()
        self._pending_requests += 1
        reply = self.network.post(
            request,
            QByteArray(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        )

        def finished() -> None:
            self._pending_requests = max(0, self._pending_requests - 1)
            self._last_request_latency_ms = max(0.0, (time.monotonic() - started) * 1000.0)
            self._last_http_status = int(reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0)
            reply.deleteLater()

        reply.finished.connect(finished)


def install_system_health_telemetry(
    window: QWidget,
    access: ApplicationAccessController,
) -> SystemHealthTelemetryController:
    existing = getattr(window, "_system_health_telemetry", None)
    if isinstance(existing, SystemHealthTelemetryController):
        return existing
    controller = SystemHealthTelemetryController(window, access)
    window._system_health_telemetry = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["SystemHealthTelemetryController", "install_system_health_telemetry"]
