from __future__ import annotations

import faulthandler
import json
import os
import platform
import sys
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime_paths import is_frozen, runtime_root

_SCHEMA_VERSION = 1
_ACTIVE_NAME = "active-session.json"
_PENDING_NAME = "pending-report.json"
_MAX_NATIVE_TAIL = 24_000

_lock = threading.RLock()
_active: dict[str, Any] | None = None
_active_path: Path | None = None
_pending_path: Path | None = None
_fault_handle: Any | None = None
_original_excepthook = sys.excepthook
_original_threading_excepthook = getattr(threading, "excepthook", None)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _diagnostic_dir() -> Path:
    target = runtime_root() / "logs" / "diagnostics"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _application_version() -> str:
    candidates: list[Path] = []
    if is_frozen():
        executable = Path(sys.executable).resolve()
        candidates.extend(
            [
                executable.parent / "_internal" / "packaging" / "VERSION",
                executable.parent / "packaging" / "VERSION",
            ]
        )
    candidates.append(Path(__file__).resolve().parents[1] / "packaging" / "VERSION")
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip().lstrip("v")
        except OSError:
            continue
        if value:
            return value[:64]
    return "0.0.0"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _native_tail(path_text: str) -> str:
    if not path_text:
        return ""
    try:
        raw = Path(path_text).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return raw[-_MAX_NATIVE_TAIL:]


def _build_pending(previous: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": _SCHEMA_VERSION,
        "crash_id": str(previous.get("crash_id") or uuid.uuid4()),
        "detected_at": _utc_now(),
        "started_at": str(previous.get("started_at") or ""),
        "last_updated_at": str(previous.get("updated_at") or ""),
        "last_stage": str(previous.get("stage") or "unknown")[:160],
        "app_version": str(previous.get("app_version") or "")[:64],
        "frozen": bool(previous.get("frozen")),
        "platform": previous.get("platform") if isinstance(previous.get("platform"), dict) else {},
        "exception": previous.get("exception") if isinstance(previous.get("exception"), dict) else {},
        "thread_exception": (
            previous.get("thread_exception")
            if isinstance(previous.get("thread_exception"), dict)
            else {}
        ),
        "native_trace_tail": _native_tail(str(previous.get("native_log") or "")),
    }


def _write_active() -> None:
    if _active is not None and _active_path is not None:
        _active["updated_at"] = _utc_now()
        _atomic_json(_active_path, _active)


def _exception_payload(exc_type: type[BaseException], exc: BaseException, tb: Any) -> dict[str, str]:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    return {
        "type": getattr(exc_type, "__name__", str(exc_type))[:160],
        "message": str(exc)[:2_000],
        "traceback": text[-24_000:],
    }


def _handle_unhandled(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
    with _lock:
        if _active is not None:
            _active["stage"] = "unhandled_exception"
            _active["exception"] = _exception_payload(exc_type, exc, tb)
            _write_active()
    _original_excepthook(exc_type, exc, tb)


def _handle_thread_exception(args: Any) -> None:
    with _lock:
        if _active is not None:
            _active["thread_exception"] = {
                "thread": str(getattr(getattr(args, "thread", None), "name", ""))[:160],
                **_exception_payload(args.exc_type, args.exc_value, args.exc_traceback),
            }
            _write_active()
    if _original_threading_excepthook is not None:
        _original_threading_excepthook(args)


def start_crash_diagnostics() -> dict[str, Any] | None:
    """Begin a crash-detectable process session and return any prior pending report.

    The active marker intentionally lives outside the versioned Velopack ``current``
    directory. A normal Qt shutdown removes it. If the process disappears without
    that cleanup, the next launch converts the stale marker into a privacy-minimal
    diagnostic report.
    """

    global _active, _active_path, _pending_path, _fault_handle

    with _lock:
        root = _diagnostic_dir()
        _active_path = root / _ACTIVE_NAME
        _pending_path = root / _PENDING_NAME

        pending = _read_json(_pending_path)
        previous = _read_json(_active_path)
        if pending is None and previous is not None and not bool(previous.get("clean_exit")):
            pending = _build_pending(previous)
            _atomic_json(_pending_path, pending)

        crash_id = str(uuid.uuid4())
        native_path = root / f"native-{crash_id}.log"
        try:
            _fault_handle = native_path.open("a", encoding="utf-8", buffering=1)
            faulthandler.enable(file=_fault_handle, all_threads=True)
        except (OSError, RuntimeError):
            _fault_handle = None

        _active = {
            "schema": _SCHEMA_VERSION,
            "crash_id": crash_id,
            "pid": os.getpid(),
            "started_at": _utc_now(),
            "updated_at": _utc_now(),
            "stage": "process_start",
            "app_version": _application_version(),
            "frozen": is_frozen(),
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version()[:240],
                "machine": platform.machine(),
            },
            "native_log": str(native_path),
            "clean_exit": False,
        }
        _write_active()
        sys.excepthook = _handle_unhandled
        if hasattr(threading, "excepthook"):
            threading.excepthook = _handle_thread_exception
        return pending


def mark_startup_stage(stage: str) -> None:
    with _lock:
        if _active is None:
            return
        _active["stage"] = str(stage or "unknown")[:160]
        _write_active()


def mark_clean_exit(reason: str = "normal") -> None:
    """Close the active diagnostic session before a known clean process exit."""

    global _fault_handle
    with _lock:
        if _active is not None:
            _active["clean_exit"] = True
            _active["stage"] = f"clean_exit:{str(reason or 'normal')[:120]}"
            _write_active()
        if _active_path is not None:
            try:
                _active_path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            faulthandler.disable()
        except RuntimeError:
            pass
        if _fault_handle is not None:
            try:
                _fault_handle.close()
            except OSError:
                pass
            _fault_handle = None


def pending_crash_report() -> dict[str, Any] | None:
    with _lock:
        if _pending_path is None:
            path = _diagnostic_dir() / _PENDING_NAME
        else:
            path = _pending_path
        return _read_json(path)


def acknowledge_pending_report(crash_id: str) -> None:
    with _lock:
        path = _pending_path or (_diagnostic_dir() / _PENDING_NAME)
        pending = _read_json(path)
        if pending is None or str(pending.get("crash_id") or "") != str(crash_id or ""):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = [
    "acknowledge_pending_report",
    "mark_clean_exit",
    "mark_startup_stage",
    "pending_crash_report",
    "start_crash_diagnostics",
]
