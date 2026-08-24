from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote_plus, urlsplit, urlunsplit

_MAX_TEXT = 12_000
_MAX_TRACEBACK = 24_000
_MAX_EVENTS = 48
_MAX_STAGE_SUMMARY = 40
_MAX_LIST = 500
_EVENT_TEXT = 2_000
_STAGE_LOG_CHUNK_CHARS = 8_000
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
    r"^(?P<type>(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*):(?:\s*(?P<message>.*))?$"
)
_EXCEPTION_TYPE_NAMES = {
    "BaseExceptionGroup",
    "ExceptionGroup",
    "GeneratorExit",
    "KeyboardInterrupt",
    "StopAsyncIteration",
    "StopIteration",
    "SystemExit",
}
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


def _sanitize_text(value: str) -> str:
    text = str(value or "")
    text = _URL_RE.sub(lambda match: sanitize_telemetry_url(match.group(0)), text)
    text = _INLINE_QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    return _BEARER_RE.sub("Bearer [REDACTED]", text)


def sanitize_telemetry_text(value: str, limit: int = _MAX_TEXT) -> str:
    return _sanitize_text(str(value or "")[: max(1, int(limit))])


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


def _discover_process_logs(
    run_dir: Path | None,
    explicit_path: str | Path | None,
) -> list[Path]:
    """Discover stage logs directly from disk, independent of UI phase labels."""

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


def _encoded_text_blob(text: str) -> dict[str, Any]:
    data = text.encode("utf-8")
    compressed = gzip.compress(data, compresslevel=6, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    chunks = [
        encoded[index : index + _STAGE_LOG_CHUNK_CHARS]
        for index in range(0, len(encoded), _STAGE_LOG_CHUNK_CHARS)
    ]
    return {
        "encoding": "gzip+base64-chunks",
        "chunks": chunks,
        "line_count": len(text.splitlines()),
        "byte_count": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "compressed_byte_count": len(compressed),
    }


def _read_complete_stage_log(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return {}, ""
    text = _sanitize_text(raw.decode("utf-8", errors="replace"))
    payload = {
        "name": path.name,
        "stage": path.stem,
        "integrity_scope": "sanitized_utf8_content",
        **_encoded_text_blob(text),
    }
    return payload, text


def _select_stage_log(paths: list[Path]) -> tuple[Path | None, dict[str, Any], str]:
    empty_candidate: tuple[Path | None, dict[str, Any], str] = (None, {}, "")
    for path in reversed(paths):
        payload, text = _read_complete_stage_log(path)
        if not payload:
            continue
        candidate = (path, payload, text)
        if text:
            return candidate
        if empty_candidate[0] is None:
            empty_candidate = candidate
    return empty_candidate


def _exception_match(raw_line: str) -> re.Match[str] | None:
    line = raw_line.strip()
    while line.startswith(("|", "+")):
        line = line[1:].lstrip()
    match = _EXCEPTION_LINE_RE.match(line)
    if not match:
        return None
    type_name = str(match.group("type") or "")
    leaf = type_name.rsplit(".", 1)[-1]
    if leaf.endswith(("Error", "Exception")) or leaf in _EXCEPTION_TYPE_NAMES:
        return match
    return None


def _extract_all_exceptions(log_text: str) -> list[dict[str, Any]]:
    exceptions: list[dict[str, Any]] = []
    for line_number, raw in enumerate(log_text.splitlines(), start=1):
        match = _exception_match(raw)
        if not match:
            continue
        sanitized_line = _sanitize_text(raw)
        exceptions.append(
            {
                "line_number": line_number,
                "error_type": sanitize_telemetry_text(str(match.group("type") or ""), 240),
                "message": sanitize_telemetry_text(str(match.group("message") or ""), _MAX_TEXT),
                "line": sanitize_telemetry_text(sanitized_line, _MAX_TEXT),
            }
        )
    return exceptions


def _extract_all_tracebacks(log_text: str) -> list[dict[str, Any]]:
    lines = log_text.splitlines()
    starts = [index for index, line in enumerate(lines) if _TRACEBACK_MARKER in line]
    tracebacks: list[dict[str, Any]] = []
    for position, start in enumerate(starts):
        next_start = starts[position + 1] if position + 1 < len(starts) else len(lines)
        end = next_start
        exception_type = ""
        exception_message = ""
        for index in range(start + 1, next_start):
            match = _exception_match(lines[index])
            if not match:
                continue
            end = index + 1
            exception_type = sanitize_telemetry_text(str(match.group("type") or ""), 240)
            exception_message = sanitize_telemetry_text(str(match.group("message") or ""), _MAX_TEXT)
            break
        block = "\n".join(lines[start:end])
        tracebacks.append(
            {
                "index": position + 1,
                "start_line": start + 1,
                "end_line": end,
                "exception_type": exception_type,
                "exception_message": exception_message,
                **_encoded_text_blob(block),
            }
        )
    return tracebacks


def _last_traceback_preview(log_text: str, tracebacks: list[dict[str, Any]]) -> str:
    if not tracebacks:
        return ""
    latest = tracebacks[-1]
    start = max(0, int(latest.get("start_line") or 1) - 1)
    end = max(start, int(latest.get("end_line") or start + 1))
    block = "\n".join(log_text.splitlines()[start:end])
    if len(block) <= _MAX_TRACEBACK:
        return sanitize_telemetry_text(block, _MAX_TRACEBACK)
    keep = max(1, _MAX_TRACEBACK - len(_TRACEBACK_MARKER) - 40)
    return sanitize_telemetry_text(
        _TRACEBACK_MARKER + "\n…[preview truncated; full traceback is encoded above]…\n" + block[-keep:],
        _MAX_TRACEBACK,
    )


def _last_nonempty_line(log_text: str) -> str:
    for raw in reversed(log_text.splitlines()):
        if raw.strip():
            return sanitize_telemetry_text(raw.strip(), _MAX_TEXT)
    return ""


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
    """Derive executor acceptance failure without using GUI progress as evidence."""

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
    """Build failure evidence with the complete stage log as the canonical truth.

    When a stage log exists, no GUI phase, manifest field, execution report, or
    exit-code fallback is allowed to replace its failure evidence. The full
    sanitized UTF-8 stage log is losslessly gzip/base64 encoded in bounded chunks
    so both the client and telemetry server can carry it without per-string
    truncation. ``line_count``, ``byte_count`` and ``sha256`` all describe the
    reconstructed sanitized stage-log bytes.
    """

    path = Path(run_dir).expanduser() if str(run_dir or "").strip() else None
    manifest: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    if path is not None:
        manifest = _read_json_object(path / "run-manifest.json")
        events = _read_diagnostic_events(path / "workflow-diagnostics.jsonl")

    process_paths = _discover_process_logs(path, process_log_path)
    stage_path, stage_log, stage_text = _select_stage_log(process_paths)
    tracebacks = _extract_all_tracebacks(stage_text) if stage_log else []
    exceptions = _extract_all_exceptions(stage_text) if stage_log else []
    traceback_preview = _last_traceback_preview(stage_text, tracebacks)

    report_path, report_payload = _latest_execution_report(artifact_roots)
    execution_failure = execution_report_failure_summary(report_payload)
    execution_report = _compact_execution_report(report_payload)

    failed_event: dict[str, Any] = {}
    for event in reversed(events):
        if str(event.get("event") or "").upper() == "FAILED" or event.get("traceback") or event.get("error_type"):
            failed_event = event
            break

    if stage_log:
        if exceptions:
            latest_exception = exceptions[-1]
            raw_error_type = str(latest_exception.get("error_type") or "StageLogException")
            raw_error_message = str(
                latest_exception.get("message")
                or latest_exception.get("line")
                or _last_nonempty_line(stage_text)
                or "stage log ended with an exception"
            )
        else:
            raw_error_type = "StageLogFailure"
            raw_error_message = _last_nonempty_line(stage_text) or "stage log captured without an explicit exception line"
        raw_failed_stage = str(stage_log.get("stage") or (stage_path.stem if stage_path is not None else "stage"))
        truth_source = "stage_log"
    elif failed_event:
        raw_error_message = str(
            failed_event.get("error")
            or failed_event.get("detail")
            or fallback_error
            or "任务失败"
        )
        raw_error_type = str(
            failed_event.get("error_type")
            or fallback_error_type
            or "TaskFailure"
        )
        raw_failed_stage = str(failed_event.get("stage") or fallback_stage or "unknown")
        truth_source = "workflow_diagnostics_fallback"
    elif execution_failure:
        raw_error_message = str(execution_failure.get("error_message") or fallback_error or "任务失败")
        raw_error_type = str(execution_failure.get("error_type") or fallback_error_type or "TaskFailure")
        raw_failed_stage = str(execution_failure.get("stage") or fallback_stage or "unknown")
        truth_source = "execution_report_fallback"
    else:
        raw_error_message = str(fallback_error or "任务失败")
        raw_error_type = str(fallback_error_type or "TaskFailure")
        raw_failed_stage = str(fallback_stage or "unknown")
        truth_source = "caller_fallback"

    error_message = sanitize_telemetry_text(raw_error_message, _MAX_TEXT)
    error_type = sanitize_telemetry_text(raw_error_type, 240)
    failed_stage = sanitize_telemetry_text(raw_failed_stage, 500)
    resolved_mode = sanitize_telemetry_text(
        str(failed_event.get("mode") or manifest.get("mode") or workflow_mode or ""),
        120,
    )

    sources = {
        "stage_log": bool(stage_log),
        "workflow_diagnostics": bool(events),
        "execution_report": bool(execution_report),
    }
    run_id = str(manifest.get("run_id") or "").strip()
    if not run_id and path is not None:
        run_id = path.name

    payload = {
        "schema": 4,
        "truth_source": truth_source,
        "run_id": run_id,
        "workflow_mode": resolved_mode,
        "failed_stage": failed_stage,
        "ui_phase": sanitize_telemetry_text(str(failed_event.get("ui_phase") or ""), 120),
        "error_type": error_type,
        "error_message": error_message,
        "traceback": traceback_preview,
        "tracebacks": tracebacks,
        "exceptions": exceptions,
        "traceback_count": len(tracebacks),
        "exception_count": len(exceptions),
        "line_count": int(stage_log.get("line_count") or 0),
        "byte_count": int(stage_log.get("byte_count") or 0),
        "sha256": str(stage_log.get("sha256") or ""),
        "active_stages": sanitize_telemetry_value(failed_event.get("active_stages") or [], max_text=240, max_list=80),
        "elapsed_seconds": float(failed_event.get("elapsed_s") or 0.0),
        "diagnostic_source_available": any(sources.values()),
        "diagnostic_sources": sources,
        "failed_event": _compact_event(failed_event) if failed_event else {},
        "execution_failure": execution_failure,
        "stage_summary": _stage_summary(events),
        "timeline": _event_timeline(events),
        "manifest": _compact_manifest(manifest),
        "stage_log_name": stage_path.name if stage_path is not None else "",
        "stage_log": stage_log,
        "available_stage_logs": [candidate.name for candidate in process_paths],
        "process_log_name": stage_path.name if stage_path is not None else "",
        "process_log_files": [candidate.name for candidate in process_paths],
        "execution_report_name": report_path.name if report_path is not None else "",
        "execution_report_run": report_path.parent.name if report_path is not None else "",
        "execution_report": execution_report,
        "fallback_context": {
            "error": sanitize_telemetry_text(fallback_error, _MAX_TEXT),
            "error_type": sanitize_telemetry_text(fallback_error_type, 240),
            "stage": sanitize_telemetry_text(fallback_stage, 500),
        },
    }
    return sanitize_telemetry_value(payload, max_text=_MAX_TRACEBACK, max_list=_MAX_LIST)


__all__ = [
    "collect_workflow_failure_diagnostic",
    "execution_report_failure_summary",
    "sanitize_telemetry_text",
    "sanitize_telemetry_url",
    "sanitize_telemetry_value",
]
