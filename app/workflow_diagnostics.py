from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from .task_control import safe_pause_point


_SECRET_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "access_token",
    "refresh_token",
)
_MAX_STRING_CHARS = 12_000
_MAX_COLLECTION_ITEMS = 100
_MAX_DEPTH = 5
_SAFE_PAUSE_EVENT_STAGES = {"source", "step1", "step2", "step3", "subprocess"}


class WorkflowDiagnostics:
    """Process-local structured diagnostics for listing orchestration only."""

    def __init__(self, run_dir: str | Path, workflow: str, **context: Any) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "workflow-diagnostics.jsonl"
        self.workflow = str(workflow or "workflow").strip() or "workflow"
        self.context: dict[str, Any] = dict(context)
        self._starts: dict[str, float] = {}
        self._sequence = 0
        self._lock = Lock()

    def update_context(self, **context: Any) -> None:
        self.context.update(context)

    def emit(self, stage: str, event: str, **details: Any) -> dict[str, Any]:
        normalized_stage = str(stage or "unknown").strip() or "unknown"
        normalized_event = str(event or "INFO").strip().upper() or "INFO"
        now = time.monotonic()
        if normalized_event == "START":
            self._starts[normalized_stage] = now
        elif normalized_event in {"COMPLETE", "FAILED", "SKIPPED"}:
            started = self._starts.pop(normalized_stage, None)
            if started is not None and "elapsed_s" not in details:
                details["elapsed_s"] = round(max(0.0, now - started), 3)

        with self._lock:
            self._sequence += 1
            body = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "seq": self._sequence,
                "workflow": self.workflow,
                "stage": normalized_stage,
                "event": normalized_event,
                **_sanitize_mapping(self.context),
                **_sanitize_mapping(details),
            }
            line = json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str)
            print("WORKFLOW_DIAG " + line, flush=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return body

    def exception(self, stage: str, exc: BaseException, **details: Any) -> dict[str, Any]:
        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        details.setdefault("active_stages", list(self._starts))
        body = self.emit(
            stage,
            "FAILED",
            error_type=type(exc).__name__,
            error=str(exc),
            traceback=trace,
            **details,
        )
        print(f"WORKFLOW_TRACEBACK stage={stage}", flush=True)
        print(trace.rstrip(), flush=True)
        return body


_current: WorkflowDiagnostics | None = None
_original_excepthook = sys.excepthook
_excepthook_installed = False


def _sensitive_key(key: str) -> bool:
    normalized = str(key or "").casefold()
    return any(fragment in normalized for fragment in _SECRET_FRAGMENTS)


def _sanitize_value(key: str, value: Any, *, depth: int = 0) -> Any:
    if _sensitive_key(key):
        return "<redacted>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        if len(value) <= _MAX_STRING_CHARS:
            return value
        return value[:_MAX_STRING_CHARS] + f"…<truncated {len(value) - _MAX_STRING_CHARS} chars>"
    if depth >= _MAX_DEPTH:
        return str(value)[:_MAX_STRING_CHARS]
    if isinstance(value, dict):
        items = list(value.items())[:_MAX_COLLECTION_ITEMS]
        output = {
            str(child_key): _sanitize_value(str(child_key), child_value, depth=depth + 1)
            for child_key, child_value in items
        }
        if len(value) > len(items):
            output["_truncated_items"] = len(value) - len(items)
        return output
    if isinstance(value, (list, tuple, set)):
        items = list(value)[:_MAX_COLLECTION_ITEMS]
        output = [_sanitize_value(key, item, depth=depth + 1) for item in items]
        if len(value) > len(items):
            output.append(f"<truncated {len(value) - len(items)} items>")
        return output
    return str(value)[:_MAX_STRING_CHARS]


def _sanitize_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _sanitize_value(str(key), value) for key, value in payload.items()}


def _emit_safely(action) -> dict[str, Any] | None:
    try:
        return action()
    except Exception as exc:
        try:
            print(f"WORKFLOW_DIAG_FALLBACK error={type(exc).__name__}: {exc}", flush=True)
        except Exception:
            pass
        return None


def _pause_checkpoint(stage: str, details: dict[str, Any]) -> str:
    if stage != "subprocess":
        return stage
    label = str(details.get("label") or "subprocess").strip()
    return "subprocess:" + "_".join(label.casefold().split())[:120]


def _pause_after_safe_event(stage: str, event: str, details: dict[str, Any]) -> None:
    if str(event or "").upper() != "COMPLETE" or stage not in _SAFE_PAUSE_EVENT_STAGES:
        return
    current = _current
    if current is None:
        return
    try:
        safe_pause_point(
            current.run_dir,
            _pause_checkpoint(stage, details),
            context={
                "workflow": current.workflow,
                "stage": stage,
                **_sanitize_mapping(details),
            },
        )
    except RuntimeError as exc:
        if "stopped while paused" in str(exc).casefold():
            raise
        print(f"TASK_CONTROL_FALLBACK error={type(exc).__name__}: {exc}", flush=True)
    except Exception as exc:
        print(f"TASK_CONTROL_FALLBACK error={type(exc).__name__}: {exc}", flush=True)


def _install_excepthook() -> None:
    global _excepthook_installed
    if _excepthook_installed:
        return

    def _diagnostic_excepthook(exc_type, exc, tb) -> None:
        current = _current
        if current is not None and isinstance(exc, BaseException):
            _emit_safely(lambda: current.exception("unhandled", exc))
        _original_excepthook(exc_type, exc, tb)

    sys.excepthook = _diagnostic_excepthook
    _excepthook_installed = True


def configure_diagnostics(
    run_dir: str | Path,
    workflow: str,
    **context: Any,
) -> WorkflowDiagnostics:
    global _current
    sink = WorkflowDiagnostics(run_dir, workflow, **context)
    _current = sink
    _install_excepthook()
    _emit_safely(lambda: sink.emit("diagnostics", "START", log_path=str(sink.path)))
    return sink


def ensure_diagnostics(
    run_dir: str | Path,
    workflow: str,
    **context: Any,
) -> WorkflowDiagnostics:
    global _current
    resolved = Path(run_dir).resolve()
    if _current is None or _current.run_dir != resolved:
        return configure_diagnostics(resolved, workflow, **context)
    _current.update_context(**context)
    return _current


def current_diagnostics() -> WorkflowDiagnostics | None:
    return _current


def diag_event(stage: str, event: str, **details: Any) -> dict[str, Any] | None:
    current = _current
    if current is None:
        return None
    normalized_stage = str(stage or "unknown").strip() or "unknown"
    normalized_event = str(event or "INFO").strip().upper() or "INFO"
    body = _emit_safely(lambda: current.emit(normalized_stage, normalized_event, **details))
    _pause_after_safe_event(normalized_stage, normalized_event, details)
    return body


def diag_exception(stage: str, exc: BaseException, **details: Any) -> dict[str, Any] | None:
    current = _current
    if current is None:
        return None
    return _emit_safely(lambda: current.exception(stage, exc, **details))


def diag_current_exception(stage: str, **details: Any) -> dict[str, Any] | None:
    exc = sys.exc_info()[1]
    if exc is None:
        return diag_event(stage, "FAILED", **details)
    return diag_exception(stage, exc, **details)


__all__ = [
    "WorkflowDiagnostics",
    "configure_diagnostics",
    "current_diagnostics",
    "diag_current_exception",
    "diag_event",
    "diag_exception",
    "ensure_diagnostics",
]
