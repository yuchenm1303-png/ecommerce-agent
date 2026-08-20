from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote_plus, urlsplit, urlunsplit

_MAX_TEXT = 12_000
_MAX_TRACEBACK = 24_000
_MAX_PROCESS_LOG = 32_000
_MAX_PER_LOG = 12_000
_MAX_EVENTS = 48
_MAX_STAGE_SUMMARY = 40
_MAX_LIST = 180
_EVENT_TEXT = 2_000
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
_BATCH_STAGE_LOGS = ("source.log", "prepare.log", "execute.log")
_FIELD_FAILURE_STATUSES = {
    "fill_error": "FieldFillError",
    "validation_failed": "FieldValidationFailure",
    "persisted_validation_failed": "FieldPersistenceFailure",
    "skipped_live_match": "FieldBindingFailure",
}
_SECTION_FAILURE_STATUSES = {
    "section_error": "SectionExecutionFailure",
    "save_failed": "SectionSaveFailure",
    "persisted_validation_failed": "SectionPersistenceFailure",
}
_PHOTO_SUCCESS_STATUSES = {"persisted_verified", "skipped"}


def sanitize_telemetry_url(value: str) -> str:
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
    try:
        size = path.stat().st_size
        read_size = min(size, max(limit * 4, 128_000))
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


def _discover_process_logs(
    run_dir: Path | None,
    explicit_path: str | Path | None,
) -> list[Path]:
    """Discover the real local process logs without trusting telemetry phase labels.

    Batch Runner already writes source/prepare/execute stdout+stderr to the job's
    diagnostics directory. Remote diagnostics must consume those files directly;
    a stale or incorrect phase label must never make the real customer log disappear.
    """

    candidates: list[Path] = []
    seen: set[str] = set()

    def add(raw: str | Path | None) -> None:
        if not str(raw or "").strip():
            return
        path = Path(raw).expanduser()
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen or not path.is_file():
            return
        seen.add(key)
        candidates.append(path)

    add(explicit_path)
    if run_dir is not None:
        for root in (run_dir / "diagnostics", run_dir.parent / "diagnostics"):
            for name in _BATCH_STAGE_LOGS:
                add(root / name)

    return sorted(candidates, key=lambda path: path.stat().st_mtime_ns)


def _process_log_payload(paths: list[Path]) -> tuple[Path | None, str, bool, list[dict[str, Any]]]:
    logs: list[dict[str, Any]] = []
    primary_path: Path | None = None
    primary_tail = ""
    primary_truncated = False

    for path in paths:
        tail, truncated = _read_text_tail(path, _MAX_PER_LOG)
        if not tail:
            continue
        logs.append(
            {
                "name": path.name,
                "tail": tail,
                "truncated": truncated,
                "size_bytes": int(path.stat().st_size),
            }
        )
        primary_path = path
        primary_tail, primary_truncated = _read_text_tail(path, _MAX_PROCESS_LOG)

    return primary_path, primary_tail, primary_truncated, logs


def _extract_traceback(log_text: str) -> str:
    marker = log_text.rfind(_TRACEBACK_MARKER)
    if marker < 0:
        return ""
    traceback_text = log_text[marker:]
    if len(traceback_text) <= _MAX_TRACEBACK:
        return sanitize_telemetry_text(traceback_text, _MAX_TRACEBACK)
    keep = max(1, _MAX_TRACEBACK - len(_TRACEBACK_MARKER) - 40)
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


def _compact_event(raw: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "ts",
        "event",
        "stage",
        "ui_phase",
        "elapsed_s",
        "detail",
        "error",
        "error_type",
        "mode",
        "active_stages",
        "context",
    )
    return sanitize_telemetry_value(
        {key: raw.get(key) for key in keys if key in raw},
        max_text=_EVENT_TEXT,
        max_list=80,
    )


def _event_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_compact_event(raw) for raw in events[-_MAX_EVENTS:]]


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
        sanitize_telemetry_value(stages[name], max_text=1_500, max_list=40)
        for name in order[-_MAX_STAGE_SUMMARY:]
    ]


def _compact_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "run_id",
        "mode",
        "product_url",
        "workflow_status",
        "vertical",
        "brand",
        "makro_target_id",
        "ownership_mode",
        "error",
        "error_type",
        "failed_stage",
        "started_at",
        "completed_at",
    )
    return sanitize_telemetry_value(
        {key: payload.get(key) for key in keys if key in payload},
        max_text=4_000,
        max_list=60,
    )


def _latest_execution_report(roots: Iterable[str | Path]) -> tuple[Path | None, dict[str, Any]]:
    candidates: list[Path] = []
    for raw in roots:
        if not str(raw or "").strip():
            continue
        root = Path(raw).expanduser()
        if root.is_file() and root.name == "report.json":
            candidates.append(root)
            continue
        if root.is_dir():
            candidates.extend(path for path in root.glob("execute-*/report.json") if path.is_file())
    if not candidates:
        return None, {}
    latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    return latest, _read_json_object(latest)


def _section_report_summary(raw: Any) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    keys = (
        "section",
        "section_title",
        "title",
        "status",
        "writes_attempted",
        "validated",
        "persisted_verified",
        "validation_failed",
        "persisted_validation_failed",
        "fill_error",
        "save_attempted",
        "saved",
        "error",
    )
    return sanitize_telemetry_value(
        {key: payload.get(key) for key in keys if key in payload},
        max_text=2_000,
        max_list=40,
    )


def _section_name(payload: dict[str, Any]) -> str:
    return str(
        payload.get("section")
        or payload.get("section_title")
        or payload.get("title")
        or "执行字段"
    ).strip()


def _field_name(payload: dict[str, Any]) -> str:
    return str(
        payload.get("label")
        or payload.get("question")
        or payload.get("attribute_key")
        or "unknown field"
    ).strip()


def _verification_detail(payload: dict[str, Any]) -> str:
    verification = payload.get("verification")
    if isinstance(verification, dict):
        detail = str(verification.get("detail") or "").strip()
        if detail:
            return detail
    return str(payload.get("detail") or payload.get("error") or "").strip()


def execution_report_failure_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Derive the canonical failure from executor acceptance evidence.

    Progress/UI phase is intentionally excluded.  A section may finish and the
    photo phase may run successfully after an earlier field failed validation;
    the acceptance report, not the last visible phase, owns failure attribution.
    """

    if not isinstance(payload, dict) or not payload:
        return {}

    sections = payload.get("section_reports")
    if isinstance(sections, list):
        for raw_section in sections:
            if not isinstance(raw_section, dict):
                continue
            section = _section_name(raw_section)
            results = raw_section.get("results")
            if isinstance(results, list):
                for raw_result in results:
                    if not isinstance(raw_result, dict):
                        continue
                    status = str(raw_result.get("execution_status") or "").strip()
                    error_type = _FIELD_FAILURE_STATUSES.get(status)
                    if not error_type:
                        continue
                    field = _field_name(raw_result)
                    detail = _verification_detail(raw_result)
                    message = detail or f"字段 {field} 执行状态={status}"
                    return sanitize_telemetry_value(
                        {
                            "source": "execution_report",
                            "stage": f"{section} / {field}",
                            "section": section,
                            "field": field,
                            "status": status,
                            "error_type": error_type,
                            "error_message": message,
                        },
                        max_text=4_000,
                        max_list=40,
                    )

            persisted = raw_section.get("persisted_verifications")
            if isinstance(persisted, list):
                for raw_verification in persisted:
                    if not isinstance(raw_verification, dict):
                        continue
                    status = str(raw_verification.get("status") or "").strip()
                    if status in {"", "persisted_verified"}:
                        continue
                    field = _field_name(raw_verification)
                    detail = str(raw_verification.get("detail") or "").strip()
                    return sanitize_telemetry_value(
                        {
                            "source": "execution_report",
                            "stage": f"{section} / {field}",
                            "section": section,
                            "field": field,
                            "status": status,
                            "error_type": "FieldPersistenceFailure",
                            "error_message": detail or f"字段 {field} Save 后验证状态={status}",
                        },
                        max_text=4_000,
                        max_list=40,
                    )

            section_status = str(raw_section.get("status") or "").strip()
            section_error_type = _SECTION_FAILURE_STATUSES.get(section_status)
            if section_error_type:
                message = str(
                    raw_section.get("save_error")
                    or raw_section.get("detail")
                    or raw_section.get("error")
                    or f"section {section} 状态={section_status}"
                ).strip()
                return sanitize_telemetry_value(
                    {
                        "source": "execution_report",
                        "stage": section,
                        "section": section,
                        "field": "",
                        "status": section_status,
                        "error_type": section_error_type,
                        "error_message": message,
                    },
                    max_text=4_000,
                    max_list=40,
                )

    photos = payload.get("photo_upload")
    if isinstance(photos, dict):
        requested = int(photos.get("requested") or 0)
        status = str(photos.get("status") or "").strip()
        if requested > 0 and status not in _PHOTO_SUCCESS_STATUSES:
            return sanitize_telemetry_value(
                {
                    "source": "execution_report",
                    "stage": "Product Photos",
                    "section": "Product Photos",
                    "field": "",
                    "status": status or "incomplete",
                    "error_type": "PhotoPersistenceFailure",
                    "error_message": str(photos.get("detail") or f"Product Photos 状态={status or 'incomplete'}"),
                },
                max_text=4_000,
                max_list=40,
            )

    completion = payload.get("completion")
    if isinstance(completion, dict):
        required_blocked = int(completion.get("required_blocked") or 0)
        if required_blocked:
            return sanitize_telemetry_value(
                {
                    "source": "execution_report",
                    "stage": "执行验收 / required fields",
                    "section": "",
                    "field": "",
                    "status": "required_blocked",
                    "error_type": "RequiredFieldBlocked",
                    "error_message": f"required_blocked={required_blocked}",
                },
                max_text=4_000,
                max_list=40,
            )
        if not completion.get("required_field_cards_persisted", True):
            return sanitize_telemetry_value(
                {
                    "source": "execution_report",
                    "stage": "执行验收 / required fields",
                    "section": "",
                    "field": "",
                    "status": "required_not_persisted",
                    "error_type": "RequiredFieldPersistenceFailure",
                    "error_message": "required sections not fully persisted",
                },
                max_text=4_000,
                max_list=40,
            )
        if not completion.get("photos_persisted", True):
            return sanitize_telemetry_value(
                {
                    "source": "execution_report",
                    "stage": "Product Photos",
                    "section": "Product Photos",
                    "field": "",
                    "status": "photos_not_persisted",
                    "error_type": "PhotoPersistenceFailure",
                    "error_message": "Product Photos not persisted",
                },
                max_text=4_000,
                max_list=40,
            )
        if not completion.get("draft_persisted_complete", True):
            return sanitize_telemetry_value(
                {
                    "source": "execution_report",
                    "stage": "执行验收",
                    "section": "",
                    "field": "",
                    "status": "acceptance_incomplete",
                    "error_type": "ExecutionAcceptanceFailure",
                    "error_message": "Full Step 3 persisted acceptance 未完整通过",
                },
                max_text=4_000,
                max_list=40,
            )

    return {}


def _compact_execution_report(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    result: dict[str, Any] = {}
    for key in (
        "mode",
        "page_url",
        "makro_target_id",
        "product_url",
        "expected_vertical",
        "plan_summary",
        "blocked_reason_summary",
        "field_totals",
        "completion",
        "section_save_attempted",
        "section_saved",
        "send_to_qc_clicked",
        "browser_closed",
        "final_screenshot",
    ):
        if key in payload:
            result[key] = payload.get(key)
    sections = payload.get("section_reports")
    if isinstance(sections, list):
        result["section_reports"] = [_section_report_summary(item) for item in sections[:12]]
    photos = payload.get("photo_upload")
    if isinstance(photos, dict):
        result["photo_upload"] = sanitize_telemetry_value(photos, max_text=2_000, max_list=60)
    failure_summary = execution_report_failure_summary(payload)
    if failure_summary:
        result["failure_summary"] = failure_summary
    return sanitize_telemetry_value(result, max_text=4_000, max_list=100)


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
    """Upload the customer's real local failure log plus structured context.

    The local process log is the primary repair evidence. Phase/timeline/report
    fields are auxiliary only. Batch logs are discovered directly from disk so a
    wrong telemetry phase can never hide the actual source/prepare/execute log.
    When the executor produced its canonical acceptance report, that report owns
    execution-failure attribution instead of the last GUI progress phase.
    """

    path = Path(run_dir).expanduser() if str(run_dir or "").strip() else None
    manifest: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    if path is not None:
        manifest = _read_json_object(path / "run-manifest.json")
        events = _read_diagnostic_events(path / "workflow-diagnostics.jsonl")

    process_paths = _discover_process_logs(path, process_log_path)
    process_path, process_log, process_log_truncated, process_logs = _process_log_payload(process_paths)

    report_path, report_payload = _latest_execution_report(artifact_roots)
    execution_failure = execution_report_failure_summary(report_payload)
    execution_report = _compact_execution_report(report_payload)

    failed_event: dict[str, Any] = {}
    for event in reversed(events):
        if str(event.get("event") or "").upper() == "FAILED" or event.get("traceback") or event.get("error_type"):
            failed_event = event
            break

    event_traceback = str(failed_event.get("traceback") or "")
    traceback_text = sanitize_telemetry_text(event_traceback, _MAX_TRACEBACK) if event_traceback else ""
    if not traceback_text:
        for item in reversed(process_logs):
            traceback_text = _extract_traceback(str(item.get("tail") or ""))
            if traceback_text:
                break

    log_error_type = ""
    log_error_message = ""
    for item in reversed(process_logs):
        log_error_type, log_error_message = _infer_exception(str(item.get("tail") or ""))
        if log_error_type or log_error_message:
            break

    if failed_event:
        raw_error_message = str(
            failed_event.get("error")
            or failed_event.get("detail")
            or log_error_message
            or fallback_error
            or "任务失败"
        )
        raw_error_type = str(
            failed_event.get("error_type")
            or log_error_type
            or fallback_error_type
            or "TaskFailure"
        )
        raw_failed_stage = str(failed_event.get("stage") or fallback_stage or "unknown")
    elif execution_failure:
        raw_error_message = str(execution_failure.get("error_message") or fallback_error or "任务失败")
        raw_error_type = str(execution_failure.get("error_type") or fallback_error_type or "TaskFailure")
        raw_failed_stage = str(execution_failure.get("stage") or fallback_stage or "unknown")
    else:
        raw_error_message = str(log_error_message or fallback_error or "任务失败")
        raw_error_type = str(log_error_type or fallback_error_type or "TaskFailure")
        raw_failed_stage = str(fallback_stage or "unknown")

    error_message = sanitize_telemetry_text(raw_error_message, _MAX_TEXT)
    error_type = sanitize_telemetry_text(raw_error_type, 240)
    failed_stage = sanitize_telemetry_text(raw_failed_stage, 500)
    resolved_mode = sanitize_telemetry_text(
        str(failed_event.get("mode") or manifest.get("mode") or workflow_mode or ""),
        120,
    )

    sources = {
        "workflow_diagnostics": bool(events),
        "process_log": bool(process_log),
        "execution_report": bool(execution_report),
    }
    run_id = str(manifest.get("run_id") or "").strip()
    if not run_id and path is not None:
        run_id = path.name

    payload = {
        "schema": 3,
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
        "failed_event": _compact_event(failed_event) if failed_event else {},
        "execution_failure": execution_failure,
        "stage_summary": _stage_summary(events),
        "timeline": _event_timeline(events),
        "manifest": _compact_manifest(manifest),
        "process_log_name": process_path.name if process_path is not None else "",
        "process_log_tail": process_log,
        "process_log_truncated": process_log_truncated,
        "process_log_files": [item.get("name") for item in process_logs],
        "process_logs": process_logs,
        "execution_report_name": report_path.name if report_path is not None else "",
        "execution_report_run": report_path.parent.name if report_path is not None else "",
        "execution_report": execution_report,
    }
    return sanitize_telemetry_value(payload, max_text=_MAX_PROCESS_LOG, max_list=_MAX_LIST)


__all__ = [
    "collect_workflow_failure_diagnostic",
    "execution_report_failure_summary",
    "sanitize_telemetry_text",
    "sanitize_telemetry_url",
    "sanitize_telemetry_value",
]
