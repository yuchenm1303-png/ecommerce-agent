from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_CONTROL_FILENAME = "task-control.json"
RUNNING = "RUNNING"
PAUSE_REQUESTED = "PAUSE_REQUESTED"
PAUSED = "PAUSED"
RESUMING = "RESUMING"
STOPPED = "STOPPED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def control_path(root: str | Path) -> Path:
    return Path(root).resolve() / TASK_CONTROL_FILENAME


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["updated_at"] = _now()
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return body


def initialize_task_control(
    root: str | Path,
    *,
    task_id: str = "",
    workflow: str = "",
    product_url: str = "",
    reset: bool = False,
) -> dict[str, Any]:
    path = control_path(root)
    existing = {} if reset else _read(path)
    if existing:
        changed = False
        for key, value in {
            "task_id": task_id,
            "workflow": workflow,
            "product_url": product_url,
        }.items():
            if value and not existing.get(key):
                existing[key] = value
                changed = True
        return _write(path, existing) if changed else existing
    return _write(
        path,
        {
            "version": 1,
            "state": RUNNING,
            "pause_requested": False,
            "task_id": task_id,
            "workflow": workflow,
            "product_url": product_url,
            "checkpoint": "",
            "checkpoint_context": {},
            "created_at": _now(),
        },
    )


def task_control_state(root: str | Path) -> dict[str, Any]:
    return _read(control_path(root))


def request_pause(
    root: str | Path,
    *,
    reason: str = "user",
    resume_kind: str = "",
) -> dict[str, Any]:
    path = control_path(root)
    payload = _read(path) or initialize_task_control(root)
    if payload.get("state") == PAUSED:
        return payload
    payload.update(
        {
            "state": PAUSE_REQUESTED,
            "pause_requested": True,
            "pause_reason": str(reason or "user"),
            "pause_requested_at": _now(),
        }
    )
    if resume_kind:
        payload["resume_kind"] = str(resume_kind)
    return _write(path, payload)


def request_resume(root: str | Path) -> dict[str, Any]:
    path = control_path(root)
    payload = _read(path) or initialize_task_control(root)
    payload.update(
        {
            "state": RESUMING,
            "pause_requested": False,
            "resume_requested_at": _now(),
        }
    )
    return _write(path, payload)


def request_stop(root: str | Path) -> dict[str, Any]:
    path = control_path(root)
    payload = _read(path) or initialize_task_control(root)
    payload.update({"state": STOPPED, "pause_requested": False, "stopped_at": _now()})
    return _write(path, payload)


def record_checkpoint(
    root: str | Path,
    checkpoint: str,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = control_path(root)
    if not path.is_file():
        return {}
    payload = _read(path)
    if not payload:
        return {}
    payload["checkpoint"] = str(checkpoint or "")
    payload["checkpoint_context"] = dict(context or {})
    payload["checkpoint_at"] = _now()
    return _write(path, payload)


def safe_pause_point(
    root: str | Path,
    checkpoint: str,
    *,
    context: dict[str, Any] | None = None,
    poll_seconds: float = 0.20,
) -> bool:
    """Cooperatively pause only after an atomic workflow unit has completed.

    The worker process stays alive and the browser remains untouched. The GUI
    changes only ``task-control.json``. When resume is requested, the caller
    continues from its normal state machine, which must re-read live browser
    state before taking the next action.
    """

    path = control_path(root)
    if not path.is_file():
        return False

    payload = record_checkpoint(root, checkpoint, context=context)
    state = str(payload.get("state") or RUNNING).upper()
    if state not in {PAUSE_REQUESTED, PAUSED}:
        return False

    payload.update(
        {
            "state": PAUSED,
            "pause_requested": True,
            "paused_at": payload.get("paused_at") or _now(),
        }
    )
    _write(path, payload)
    print(f"GUI_TASK_STATE PAUSED checkpoint={checkpoint}", flush=True)

    while True:
        current = _read(path)
        state = str(current.get("state") or RUNNING).upper()
        if state == STOPPED:
            raise RuntimeError("Task was stopped while paused")
        if state in {RESUMING, RUNNING} and not bool(current.get("pause_requested")):
            current.update(
                {
                    "state": RUNNING,
                    "pause_requested": False,
                    "resumed_at": _now(),
                }
            )
            _write(path, current)
            print(f"GUI_TASK_STATE RUNNING checkpoint={checkpoint}", flush=True)
            return True
        time.sleep(max(0.05, float(poll_seconds)))


__all__ = [
    "PAUSED",
    "PAUSE_REQUESTED",
    "RESUMING",
    "RUNNING",
    "STOPPED",
    "TASK_CONTROL_FILENAME",
    "control_path",
    "initialize_task_control",
    "record_checkpoint",
    "request_pause",
    "request_resume",
    "request_stop",
    "safe_pause_point",
    "task_control_state",
]
