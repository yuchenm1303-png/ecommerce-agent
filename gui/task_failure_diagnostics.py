from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote_plus, urlsplit, urlunsplit

_MAX_TEXT = 12_000
_MAX_TRACEBACK = 32_000
_MAX_PROCESS_LOG = 64_000
_MAX_EVENTS = 96
_MAX_LIST = 180
_SECRET_KEY_RE = re.compile(
    r"(^|_)(api[_-]?key|token|secret|password|authorization|cookie|refresh[_-]?token|access[_-]?token)($|_)",
    re.IGNORECASE,
)
_SECRET_QUERY_RE = re.compile(
    r"^(?:api[_-]?key|key|token|access[_-]?token|refresh[_-]?token|secret|password|passwd|pwd|authorization|auth|signature|sig|sign|credential|session|sessionid)$",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_INLINE_QUERY_SECRET_RE = re.compile(
    r"([?&](?:api[_-]?key|key|token|access[_-]?token|refresh[_-]?token|secret|password|passwd|pwd|authorization|auth|signature|sig|sign|credential|session|sessionid)=)([^&#\s\"'<>]+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+\-/=]{8,}", re.IGNORECASE)
_EXCEPTION_LINE_RE = re.compile(
    r"^(?P<type>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):\s*(?P<message>.*)$"
)
_TRACEBACK_MARKER = "Traceback (most recent call last):"


def sanitize_telemetry_url(value: str) -> str:
    """Redact secret-like query values without changing non-secret URL context."""

    raw = str(value or "")
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return _INLINE_QUERY_SECRET_RE.sub(r"\1[REDACTED]", raw)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.netloc:
        return _INLINE_QUERY_SECRET_RE.sub(r"\1[REDACTED]", raw)

    query_parts: list[str] = []
    for item in parts.query.split("&") if parts.query else []:
        name, sep, _value = item.partition("=")
        try:
            decoded_name = unquote_plus(name).strip()
        except Exception:
            decoded_name = name.strip()
        if sep and _SECRET_QUERY_RE.fullmatch(decoded_name):
            query_parts.append(f"{name}=[REDACTED]")
        else:
            query_parts.append(item)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(query_parts), parts.fragment))


def sanitize_telemetry_text(value: str, limit: int = _MAX_TEXT) -> str:
    """Sanitize URLs and obvious bearer credentials embedded in diagnostic text."""

    text = str(value or "")[: max(1, int(limit))]
    text = _URL_RE.sub(lambda match: sanitize_telemetry_url(match.group(0)), text)
    text = _INLINE_QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    return _BEARER_RE.sub("Bearer [REDACTED]", text)


def sanitize_telemetry_value(
    value: Any,
    *,
    depth: int = 0,
    max_text: int = _MAX_TEXT,
    max_list: int = _MAX_LIST,
) -> Any:
    """Recursively sanitize owner telemetry while preserving diagnostic structure."""

    if depth > 10:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Path):
        return value.name
    if isinstance(value, str):
        return sanitize_telemetry_text(value, max_text)
    if isinstance(value, (list, tuple, set)):
        return [
            sanitize_telemetry_value(item, depth=depth + 1, max_text=max_text, max_list=max_list)
            for item in list(value)[:max_list]
        ]
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in list(value.items())[:max_list]:
            name = sanitize_telemetry_text(str(key or ""), 160)
            output[name] = (
                "[REDACTED]"
                if _SECRET_KEY_RE.search(name)
                else sanitize_telemetry_value(
                    item,
                    depth=depth + 1,
                    max_text=max_text,
                    max_list=max_list,
                )
            )
        return output
    if hasattr(value, "__dict__"):
        return sanitize_telemetry_value(
            vars(value),
            depth=depth + 1,
            max_text=max_text,
            max_list=max_list,
        )
    return sanitize_telemetry_text(str(value), max_text)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_diagnostic_events(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-_MAX_EVENTS:]:
        try:
            payload = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _read_text_tail(path: Path, limit: int = _MAX_PROCESS_LOG) -> tuple[str, bool]:
    """Read the diagnostically useful tail without loading an unbounded log file."""

    try:
        size = path.stat().st_size
        read_size = min(size, max(limit * 4, 256_000))
        with path.open("rb") as handle:
            if size > read_size:
                handle.seek(-read_size, 2)
            raw = handle.read(read_size)
    except OSError:
        return "", False

    text = raw.decode("utf-8", errors="replace")
    truncated = size > read_size or len(text) > limit
    if len(text) > limit:
        text = text[-limit:]
    return sanitize_telemetry_text(text, limit), truncated


def _extract_traceback(log_text: str) -> str:
    marker = log_text.rfind(_TRACEBACK_MARKER)
    if marker < 0:
        return ""
    traceback_text = log_text[marker:]
    if len(traceback_text) <= _MAX_TRACEBACK:
        return sanitize_telemetry_text(traceback_text, _MAX_TRACEBACK)
    keep = max(1, _MAX_TRACEBACK - len(_TRACEBACK_MARKER) - 2)
    return sanitize_telemetry_text(
        _TRACEBACK_MARKER + "\n…[traceback middle truncated]…\n" + traceback_text[-keep:],
        _MAX_TRACEBACK,
    )


def _infer_exception(log_text: str) -> tuple[str, str]:
    for raw in reversed(log_text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        match = _EXCEPTION_LINE_RE.match(line)
        if match:
            return (
                sanitize_telemetry_text(match.group("type"), 240),
                sanitize_telemetry_text(match.group("message") or line, _MAX_TEXT),
            )
    return "", ""


def _event_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for raw in events[-_MAX_EVENTS:]:
        event = dict(raw)
        if event.get("traceback"):
            event["traceback"] = "[see failure_diagnostic.traceback]"
        if event.get("process_log_tail"):
            event["process_log_tail"] = "[see failure_diagnostic.process_log_tail]"
        timeline.append(
            sanitize_telemetry_value(event, max_text=_MAX_TEXT, max_list=_MAX_LIST)
        )
    return timeline


def _stage_summary(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stages: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        stage = str(event.get("stage") or "").strip()
        if not stage:
            continue
        if stage not in stages:
            order.append(stage)
        stages[stage] = {
            "stage": stage,
            "event": str(event.get("event") or ""),
            "ts": str(event.get("ts") or ""),
            "elapsed_seconds": float(event.get("elapsed_s") or 0.0),
            "ui_phase": str(event.get("ui_phase") or ""),
            "detail": str(event.get("detail") or event.get("error") or ""),
        }
    return [
        sanitize_telemetry_value(stages[name], max_text=4_000, max_list=40)
        for name in order[-80:]
    ]


def _latest_execution_report(roots: Iterable[str | Path]) -> tuple[Path | None, dict[str, Any]]:
    candidates: list[Path] = []
    for raw in roots:
        if not str(raw or "").strip():
            continue
        root = Path(raw).expanduser()
        if root.is_file() and root.name == "report.json":
            candidates.append(root)
            continue
        if not root.is_dir():
            continue
        candidates.extend(path for path in root.glob("execute-*/report.json") if path.is_file())
    if not candidates:
        return None, {}
    latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    return latest, _read_json_object(latest)


def _compact_execution_report(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    keys = (
        "mode",
        "page_url",
        "makro_target_id",
        "product_url",
        "expected_vertical",
        "plan_summary",
        "blocked_reason_summary",
        "section_reports",
        "field_totals",
        "photo_upload",
        "completion",
        "section_save_attempted",
        "section_saved",
        "send_to_qc_clicked",
        "browser_closed",
        "final_screenshot",
    )
    return sanitize_telemetry_value(
        {key: payload.get(key) for key in keys if key in payload},
        max_text=_MAX_TEXT,
        max_list=_MAX_LIST,
    )


def collect_workflow_failure_diagnostic(
    run_dir: str | Path | None,
    *,
    fallback_error: str = "",
    fallback_error_type: str = "",
    fallback_stage: str = "",
    workflow_mode: str = "",
    process_log_path: str | Path | None = None,
    artifact_roots: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Build an owner-visible diagnosis that is sufficient to repair a failed task.

    Structured workflow events remain the canonical source for prepare failures.
    Real execution failures additionally consume the exact merged child-process
    log and any executor report that survived the failure. Secrets are redacted
    before upload and log size is deterministically bounded per product audit.
    """

    path = Path(run_dir).expanduser() if str(run_dir or "").strip() else None
    manifest: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    if path is not None:
        manifest = _read_json_object(path / "run-manifest.json")
        events = _read_diagnostic_events(path / "workflow-diagnostics.jsonl")

    process_path = Path(process_log_path).expanduser() if str(process_log_path or "").strip() else None
    process_log = ""
    process_log_truncated = False
    if process_path is not None:
        process_log, process_log_truncated = _read_text_tail(process_path)

    report_path, report_payload = _latest_execution_report(artifact_roots)
    execution_report = _compact_execution_report(report_payload)

    failed_event: dict[str, Any] = {}
    for event in reversed(events):
        if str(event.get("event") or "").upper() == "FAILED" or event.get("traceback") or event.get("error_type"):
            failed_event = event
            break

    event_traceback = str(failed_event.get("traceback") or "")
    traceback_text = sanitize_telemetry_text(event_traceback, _MAX_TRACEBACK) if event_traceback else _extract_traceback(process_log)
    log_error_type, log_error_message = _infer_exception(process_log)

    error_message = sanitize_telemetry_text(
        str(
            failed_event.get("error")
            or failed_event.get("detail")
            or log_error_message
            or fallback_error
            or "任务失败"
        ),
        _MAX_TEXT,
    )
    error_type = sanitize_telemetry_text(
        str(failed_event.get("error_type") or log_error_type or fallback_error_type or "TaskFailure"),
        240,
    )
    failed_stage = sanitize_telemetry_text(
        str(failed_event.get("stage") or fallback_stage or "unknown"),
        240,
    )
    resolved_mode = sanitize_telemetry_text(
        str(
            failed_event.get("mode")
            or manifest.get("mode")
            or workflow_mode
            or ""
        ),
        120,
    )

    failed_event_safe = dict(failed_event)
    if failed_event_safe.get("traceback"):
        failed_event_safe["traceback"] = "[see failure_diagnostic.traceback]"
    if failed_event_safe.get("process_log_tail"):
        failed_event_safe["process_log_tail"] = "[see failure_diagnostic.process_log_tail]"

    sources = {
        "workflow_diagnostics": bool(events),
        "process_log": bool(process_log),
        "execution_report": bool(execution_report),
    }
    run_id = str(manifest.get("run_id") or "").strip()
    if not run_id and path is not None:
        run_id = path.name

    payload = {
        "schema": 2,
        "run_id": run_id,
        "workflow_mode": resolved_mode,
        "failed_stage": failed_stage,
        "ui_phase": sanitize_telemetry_text(str(failed_event.get("ui_phase") or ""), 120),
        "error_type": error_type,
        "error_message": error_message,
        "traceback": traceback_text,
        "active_stages": sanitize_telemetry_value(failed_event.get("active_stages") or [], max_text=240, max_list=80),
        "elapsed_seconds": float(failed_event.get("elapsed_s") or 0.0),
        "diagnostic_source_available": any(sources.values()),
        "diagnostic_sources": sources,
        "failed_event": sanitize_telemetry_value(failed_event_safe, max_text=_MAX_TEXT, max_list=_MAX_LIST),
        "stage_summary": _stage_summary(events),
        "timeline": _event_timeline(events),
        "manifest": sanitize_telemetry_value(manifest, max_text=_MAX_TEXT, max_list=_MAX_LIST),
        "process_log_name": process_path.name if process_path is not None else "",
        "process_log_tail": process_log,
        "process_log_truncated": process_log_truncated,
        "execution_report_name": report_path.name if report_path is not None else "",
        "execution_report_run": report_path.parent.name if report_path is not None else "",
        "execution_report": execution_report,
    }
    return sanitize_telemetry_value(payload, max_text=_MAX_PROCESS_LOG, max_list=_MAX_EVENTS)


__all__ = [
    "collect_workflow_failure_diagnostic",
    "sanitize_telemetry_text",
    "sanitize_telemetry_url",
    "sanitize_telemetry_value",
]
