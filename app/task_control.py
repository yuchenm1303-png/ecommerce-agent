from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_CONTROL_FILENAME = "task-control.json"
TASK_COMMAND_FILENAME = "task-command.json"
RUNNING = "RUNNING"
PAUSE_REQUESTED = "PAUSE_REQUESTED"
PAUSED = "PAUSED"
RESUMING = "RESUMING"
STOPPED = "STOPPED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def control_path(root: str | Path) -> Path:
    return Path(root).resolve() / TASK_CONTROL_FILENAME


def command_path(root: str | Path) -> Path:
    return Path(root).resolve() / TASK_COMMAND_FILENAME


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
    state_path = control_path(root)
    existing = {} if reset else _read(state_path)
    if reset:
        _write(command_path(root), {"version": 1, "command": RUNNING, "created_at": _now()})
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
        return _write(state_path, existing) if changed else existing
    payload = _write(
        state_path,
        {
            "version": 1,
            "state": RUNNING,
            "task_id": task_id,
            "workflow": workflow,
            "product_url": product_url,
            "checkpoint": "",
            "checkpoint_context": {},
            "created_at": _now(),
        },
    )
    if not command_path(root).is_file():
        _write(command_path(root), {"version": 1, "command": RUNNING, "created_at": _now()})
    return payload


def task_control_state(root: str | Path) -> dict[str, Any]:
    state = _read(control_path(root))
    command = _read(command_path(root))
    if not state:
        return {}
    effective = dict(state)
    current_state = str(state.get("state") or RUNNING).upper()
    current_command = str(command.get("command") or RUNNING).upper()
    if current_command == "PAUSE" and current_state != PAUSED:
        effective["state"] = PAUSE_REQUESTED
    elif current_command == "RESUME" and current_state == PAUSED:
        effective["state"] = RESUMING
    elif current_command == "STOP":
        effective["state"] = STOPPED
    if command.get("resume_kind"):
        effective["resume_kind"] = command.get("resume_kind")
    return effective


def request_pause(
    root: str | Path,
    *,
    reason: str = "user",
    resume_kind: str = "",
) -> dict[str, Any]:
    initialize_task_control(root)
    payload: dict[str, Any] = {
        "version": 1,
        "command": "PAUSE",
        "reason": str(reason or "user"),
        "requested_at": _now(),
    }
    if resume_kind:
        payload["resume_kind"] = str(resume_kind)
    _write(command_path(root), payload)
    return task_control_state(root)


def request_resume(root: str | Path) -> dict[str, Any]:
    initialize_task_control(root)
    _write(
        command_path(root),
        {"version": 1, "command": "RESUME", "requested_at": _now()},
    )
    return task_control_state(root)


def request_stop(root: str | Path) -> dict[str, Any]:
    initialize_task_control(root)
    _write(
        command_path(root),
        {"version": 1, "command": "STOP", "requested_at": _now()},
    )
    return task_control_state(root)


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
    """Cooperatively pause after an atomic workflow unit has completed.

    The worker owns ``task-control.json`` and the GUI owns ``task-command.json``.
    Keeping command and state writes separate avoids cross-process lost updates.
    The browser is never frozen; after RESUME the caller continues through its
    normal live-state reconciliation before taking the next browser action.
    """

    state_path = control_path(root)
    if not state_path.is_file():
        return False

    state = record_checkpoint(root, checkpoint, context=context)
    command = _read(command_path(root))
    action = str(command.get("command") or RUNNING).upper()
    if action == "STOP":
        raise RuntimeError("Task was stopped at safe checkpoint")
    if action != "PAUSE" and str(state.get("state") or RUNNING).upper() != PAUSED:
        return False

    state.update(
        {
            "state": PAUSED,
            "paused_at": state.get("paused_at") or _now(),
        }
    )
    if command.get("resume_kind"):
        state["resume_kind"] = command.get("resume_kind")
    _write(state_path, state)
    print(f"GUI_TASK_STATE PAUSED checkpoint={checkpoint}", flush=True)

    while True:
        command = _read(command_path(root))
        action = str(command.get("command") or RUNNING).upper()
        if action == "STOP":
            raise RuntimeError("Task was stopped while paused")
        if action in {"RESUME", RUNNING}:
            current = _read(state_path) or state
            current.update(
                {
                    "state": RUNNING,
                    "resumed_at": _now(),
                }
            )
            _write(state_path, current)
            print(f"GUI_TASK_STATE RUNNING checkpoint={checkpoint}", flush=True)
            return True
        time.sleep(max(0.05, float(poll_seconds)))


__all__ = [
    "PAUSED",
    "PAUSE_REQUESTED",
    "RESUMING",
    "RUNNING",
    "STOPPED",
    "TASK_COMMAND_FILENAME",
    "TASK_CONTROL_FILENAME",
    "command_path",
    "control_path",
    "initialize_task_control",
    "record_checkpoint",
    "request_pause",
    "request_resume",
    "request_stop",
    "safe_pause_point",
    "task_control_state",
]
