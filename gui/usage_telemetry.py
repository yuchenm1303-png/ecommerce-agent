from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QApplication, QWidget

from .app_access import ApplicationAccessController
from .task_failure_diagnostics import (
    collect_workflow_failure_diagnostic,
    sanitize_telemetry_value,
)


_HEARTBEAT_MS = 60_000
_AUDIT_FLUSH_MS = 1_200
_MAX_TEXT = 12_000
_MAX_AUDIT_TEXT = 32_000
_MAX_LIST = 180
_BATCH_EXECUTE_ACTIVE = {"FILLING", "UPLOADING_IMAGES", "SAVING", "VERIFYING"}
_EXECUTOR_LOCAL_ARTIFACT_KEYS = {
    "path",
    "live_schema",
    "_report_path",
    "decision_packet",
    "required_override_file",
    "final_screenshot",
    "evidence_images",
    "source_snapshots",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object, limit: int = _MAX_TEXT) -> str:
    return str(value or "").strip()[: max(1, int(limit))]


def _safe_value(
    value: Any,
    *,
    depth: int = 0,
    max_text: int = _MAX_TEXT,
) -> Any:
    return sanitize_telemetry_value(
        value,
        depth=depth,
        max_text=max_text,
        max_list=_MAX_LIST,
    )


def _widget_text(widget: Any) -> str:
    getter = getattr(widget, "text", None)
    if not callable(getter):
        return ""
    try:
        return _text(getter())
    except Exception:
        return ""


def _file_metadata_entry(raw: Any) -> dict[str, Any] | None:
    try:
        path = Path(raw).expanduser().resolve()
    except Exception:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {
        "name": path.name,
        "extension": path.suffix.casefold(),
        "size_bytes": int(size),
    }


def _file_metadata(values: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in list(values or ())[:100]:
        item = _file_metadata_entry(raw)
        if item is not None:
            output.append(item)
    return output


def _optional_int(payload: Any, key: str) -> int | None:
    if not isinstance(payload, dict) or key not in payload:
        return None
    try:
        return int(payload.get(key))
    except (TypeError, ValueError, OverflowError):
        return None


def _privacy_safe_executor_value(value: Any, *, depth: int = 0) -> Any:
    """Keep executor evidence while dropping local filesystem artifact locations."""

    if depth > 10:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in list(value.items())[:_MAX_LIST]:
            name = str(key or "")
            folded = name.casefold()
            if (
                folded in _EXECUTOR_LOCAL_ARTIFACT_KEYS
                or folded.endswith("_path")
                or folded.startswith("screenshot_")
            ):
                continue
            output[name] = _privacy_safe_executor_value(item, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple, set)):
        return [
            _privacy_safe_executor_value(item, depth=depth + 1)
            for item in list(value)[:_MAX_LIST]
        ]
    return value


def _compact_photo_upload(raw: Any) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    if not payload:
        return {}

    result: dict[str, Any] = {}
    for key in (
        "status",
        "detail",
        "requested",
        "attempted",
        "staged",
        "persisted",
        "final_count",
        "initial_count",
        "already_persisted",
        "capacity",
        "save_attempted",
        "saved",
        "save_count",
    ):
        if key in payload:
            result[key] = payload.get(key)

    persistence = payload.get("persistence")
    if isinstance(persistence, dict):
        result["persistence"] = {
            key: persistence.get(key)
            for key in ("status", "detail", "final_count", "initial_count", "expected_added")
            if key in persistence
        }

    reopened = payload.get("reopened_state")
    if isinstance(reopened, dict):
        result["reopened_state"] = {
            key: reopened.get(key)
            for key in ("capacity", "completion_count", "visible_image_count", "remaining_empty_slots")
            if key in reopened
        }

    items: list[dict[str, Any]] = []
    for raw_item in list(payload.get("items") or ())[:20]:
        if not isinstance(raw_item, dict):
            continue
        path_value = raw_item.get("path")
        metadata = _file_metadata_entry(path_value) if str(path_value or "").strip() else None
        item: dict[str, Any] = {
            key: raw_item.get(key)
            for key in (
                "index",
                "status",
                "slot_position",
                "before_empty_slots",
                "after_empty_slots",
                "before_completion_count",
            )
            if key in raw_item
        }
        if metadata is not None:
            item.update(metadata)
        elif raw_item.get("name"):
            item["name"] = Path(str(raw_item.get("name"))).name
        items.append(item)
    if items:
        result["items"] = items

    return _safe_value(result, max_text=2_000)


def _material_usage_payload(
    selected_files: Any,
    executor_report: Any,
) -> dict[str, Any]:
    selected = [
        _safe_value(item, max_text=1_000)
        for item in list(selected_files or ())[:100]
        if isinstance(item, dict)
    ]
    report = executor_report if isinstance(executor_report, dict) else {}
    photo = report.get("photo_upload") if isinstance(report.get("photo_upload"), dict) else {}
    report_items = [item for item in list(photo.get("items") or ())[:20] if isinstance(item, dict)]

    requested = _optional_int(photo, "requested")
    attempted = _optional_int(photo, "attempted")
    staged = _optional_int(photo, "staged")
    persisted = _optional_int(photo, "persisted")
    final_count = _optional_int(photo, "final_count")
    already_persisted = _optional_int(photo, "already_persisted")
    confirmed_saved = max(persisted or 0, final_count or 0, already_persisted or 0)
    detected = max(
        len(selected),
        len(report_items),
        requested or 0,
        attempted or 0,
        staged or 0,
    )
    has_execution_evidence = bool(photo)

    if confirmed_saved > 0:
        state = "used"
        evidence = "executor_report"
    elif detected > 0:
        state = "detected"
        evidence = "executor_report" if has_execution_evidence else "gui_snapshot"
    elif has_execution_evidence and requested == 0 and attempted == 0:
        state = "none"
        evidence = "executor_report"
    else:
        state = "unknown"
        evidence = "none"

    actual_files = [
        {
            key: item.get(key)
            for key in ("name", "extension", "size_bytes", "index", "status", "slot_position")
            if key in item
        }
        for item in report_items
    ]
    return _safe_value(
        {
            "schema": 1,
            "state": state,
            "evidence": evidence,
            "selected_file_count": len(selected),
            "selected_files": selected,
            "photo_requested": requested,
            "photo_attempted": attempted,
            "photo_staged": staged,
            "photo_persisted": persisted,
            "photo_final_count": final_count,
            "photo_confirmed_saved": confirmed_saved,
            "actual_files": actual_files,
        },
        max_text=2_000,
    )


def _material_report(result_data: dict[str, Any]) -> dict[str, Any]:
    report = result_data.get("executor_report")
    if isinstance(report, dict):
        return report
    diagnostic = result_data.get("failure_diagnostic")
    if isinstance(diagnostic, dict):
        failure_report = diagnostic.get("execution_report")
        if isinstance(failure_report, dict):
            return failure_report
    return {}


def _with_material_usage(
    result_data: dict[str, Any],
    selected_files: Any,
) -> dict[str, Any]:
    output = dict(result_data)
    output["material_usage"] = _material_usage_payload(selected_files, _material_report(output))
    return _safe_value(output, max_text=_MAX_AUDIT_TEXT)


def _phase_stats(stats: Any) -> dict[str, Any]:
    return {
        "batch_count": int(getattr(stats, "batch_count", 0) or 0),
        "model_calls": int(getattr(stats, "model_calls", 0) or 0),
        "cache_hits": int(getattr(stats, "cache_hits", 0) or 0),
        "failed_batches": int(getattr(stats, "failed_batches", 0) or 0),
        "source_cache_hit": bool(getattr(stats, "source_cache_hit", False)),
        "web_batch_count": int(getattr(stats, "web_batch_count", 0) or 0),
        "web_model_calls": int(getattr(stats, "web_model_calls", 0) or 0),
        "web_cache_hits": int(getattr(stats, "web_cache_hits", 0) or 0),
        "web_failed_batches": int(getattr(stats, "web_failed_batches", 0) or 0),
        "elapsed_seconds": float(getattr(stats, "elapsed_seconds", 0.0) or 0.0),
    }


def _compact_executor_report(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
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
        "completion",
        "section_save_attempted",
        "section_saved",
        "send_to_qc_clicked",
        "browser_closed",
    )
    result: dict[str, Any] = {
        key: _privacy_safe_executor_value(payload.get(key))
        for key in keys
        if key in payload
    }
    photo_upload = _compact_photo_upload(payload.get("photo_upload"))
    if photo_upload:
        result["photo_upload"] = photo_upload
    return _safe_value(result, max_text=_MAX_AUDIT_TEXT)


def _run_result_payload(
    result: Any,
    *,
    selected_files: Any = (),
) -> dict[str, Any]:
    if result is None or not hasattr(result, "run_dir"):
        return {}

    fields: list[dict[str, Any]] = []
    for field in list(getattr(result, "fields", ()) or ())[:160]:
        fields.append(
            {
                "field_id": _text(getattr(field, "field_id", ""), 240),
                "field_name": _text(getattr(field, "field_name", ""), 500),
                "ai_result": _text(getattr(field, "ai_result", ""), 8_000),
                "ai_status": _text(getattr(field, "ai_status", ""), 80),
                "final_status": _text(getattr(field, "final_status", ""), 80),
                "blocked_reason": _text(getattr(field, "blocked_reason", ""), 4_000),
                "source": _text(getattr(field, "source", ""), 8_000),
            }
        )

    web_candidates: list[dict[str, Any]] = []
    for item in list(getattr(result, "web_candidates", ()) or ())[:80]:
        web_candidates.append(
            {
                "url": _text(getattr(item, "url", ""), 4_096),
                "title": _text(getattr(item, "title", ""), 1_000),
                "match": _text(getattr(item, "match", ""), 80),
                "reason": _text(getattr(item, "reason", ""), 2_000),
                "identity_evidence": _safe_value(getattr(item, "identity_evidence", []) or []),
            }
        )

    safety = getattr(result, "safety", None)
    executor_report = _compact_executor_report(getattr(result, "executor_report", {}) or {})
    payload = {
        "workflow_mode": _text(getattr(result, "workflow_mode", ""), 80),
        "workflow_status": _text(getattr(result, "workflow_status", ""), 120),
        "product_url": _text(getattr(result, "product_url", ""), 4_096),
        "vertical": _text(getattr(result, "vertical", ""), 500),
        "brand": _text(getattr(result, "brand", ""), 500),
        "ready": int(getattr(result, "ready", 0) or 0),
        "missing": int(getattr(result, "missing", 0) or 0),
        "conflict": int(getattr(result, "conflict", 0) or 0),
        "blocked": int(getattr(result, "blocked", 0) or 0),
        "live_field_count": int(getattr(result, "live_field_count", 0) or 0),
        "cold": _phase_stats(getattr(result, "cold", None)),
        "hot": _phase_stats(getattr(result, "hot", None)),
        "plan_summary": _safe_value(getattr(result, "plan_summary", {}) or {}),
        "safety": {
            "writes_performed": int(getattr(safety, "writes_performed", 0) or 0),
            "save_clicked": bool(getattr(safety, "save_clicked", False)),
            "send_to_qc_clicked": bool(getattr(safety, "send_to_qc_clicked", False)),
        },
        "fields": fields,
        "web_candidates": web_candidates,
        "executor_report": executor_report,
        "run_id": Path(getattr(result, "run_dir")).name,
    }
    return _with_material_usage(payload, selected_files)


def _first_result(args: tuple[Any, ...]) -> Any | None:
    for value in args:
        if hasattr(value, "run_dir") and hasattr(value, "fields"):
            return value
    return None


def _first_mapping(args: tuple[Any, ...]) -> dict[str, Any] | None:
    for value in args:
        if isinstance(value, dict):
            return value
    return None


def _error_text(args: tuple[Any, ...]) -> str:
    for value in args:
        if isinstance(value, BaseException):
            return _text(value, 12_000)
        if isinstance(value, str) and value.strip():
            return _text(value, 12_000)
    return "任务失败"


def _error_type(args: tuple[Any, ...]) -> str:
    for value in args:
        if isinstance(value, BaseException):
            return type(value).__name__[:240]
    return "TaskFailure"


def _runner_workflow_dir(runner: Any) -> str:
    config = getattr(runner, "config", None) if runner is not None else None
    read_only_run_dir = getattr(config, "read_only_run_dir", None) if config is not None else None
    if str(read_only_run_dir or "").strip():
        return str(read_only_run_dir)
    for name in ("run_dir", "output_dir", "last_run_dir"):
        value = getattr(runner, name, None) if runner is not None else None
        if str(value or "").strip():
            return str(value)
    return ""


def _runner_process_log(runner: Any) -> str:
    output_root = getattr(runner, "output_root", None) if runner is not None else None
    if not str(output_root or "").strip():
        return ""
    return str(Path(output_root) / "real-execution-gui.log")


def _runner_artifact_roots(runner: Any) -> tuple[str, ...]:
    output_root = getattr(runner, "output_root", None) if runner is not None else None
    return (str(output_root),) if str(output_root or "").strip() else ()


def _runner_mode(runner: Any) -> str:
    config = getattr(runner, "config", None) if runner is not None else None
    return _text(
        getattr(config, "scope", None)
        or getattr(runner, "mode", None)
        or "",
        120,
    )


def _failure_result(
    result_data: dict[str, Any],
    *,
    runner: Any,
    args: tuple[Any, ...],
    fallback_stage: str,
) -> dict[str, Any]:
    output = dict(result_data)
    output["failure_diagnostic"] = collect_workflow_failure_diagnostic(
        _runner_workflow_dir(runner),
        fallback_error=_error_text(args),
        fallback_error_type=_error_type(args),
        fallback_stage=fallback_stage,
        workflow_mode=_runner_mode(runner),
        process_log_path=_runner_process_log(runner),
        artifact_roots=_runner_artifact_roots(runner),
    )
    return _safe_value(output, max_text=_MAX_AUDIT_TEXT)


def _batch_jobs(batch: Any) -> list[Any]:
    return list(getattr(batch, "jobs", ()) or ())


def _batch_operation_job_ids(batch: Any, event_type: str) -> tuple[str, ...]:
    jobs = _batch_jobs(batch)
    if event_type == "batch_execute":
        jobs = [job for job in jobs if str(getattr(job, "status", "") or "").upper() == "READY"]
    return tuple(
        _text(getattr(job, "job_id", ""), 160)
        for job in jobs
        if _text(getattr(job, "job_id", ""), 160)
    )


def _batch_cohort_statuses(batch: Any, job_ids: tuple[str, ...]) -> list[str]:
    wanted = set(job_ids)
    return [
        str(getattr(job, "status", "") or "").upper()
        for job in _batch_jobs(batch)
        if _text(getattr(job, "job_id", ""), 160) in wanted
    ]


def _batch_terminal_semantics(
    event_type: str,
    batch: Any,
    job_ids: tuple[str, ...],
) -> tuple[str, str]:
    """Return coarse batch outcome while product audits remain per-link."""

    batch_status = str(getattr(batch, "status", "") or "").upper()
    statuses = _batch_cohort_statuses(batch, job_ids)
    expected_batch_status = "PREPARED" if event_type == "batch_prepare" else "COMPLETE"
    expected_job_status = "READY" if event_type == "batch_prepare" else "DONE"
    audit_success = "ready" if event_type == "batch_prepare" else "completed"

    if batch_status == expected_batch_status and statuses and all(
        status == expected_job_status for status in statuses
    ):
        if event_type == "batch_execute":
            all_statuses = [str(getattr(job, "status", "") or "").upper() for job in _batch_jobs(batch)]
            if any(status in {"FAILED", "REVIEW", "STOPPED"} for status in all_statuses):
                return "completed", "review"
        return "completed", audit_success

    successful = sum(status == expected_job_status for status in statuses)
    hard_failed = any(status in {"FAILED", "STOPPED"} for status in statuses)
    if successful > 0:
        return "failed", "review"
    return "failed", "failed" if hard_failed or not statuses else "review"


def _batch_job_status(event_type: str, job_status: str) -> str:
    status = str(job_status or "").upper()
    if event_type == "batch_execute":
        if status == "DONE":
            return "completed"
        if status == "REVIEW":
            return "review"
        if status == "FAILED":
            return "failed"
        if status == "STOPPED":
            return "cancelled"
        return "running"
    if status == "READY":
        return "ready"
    if status == "REVIEW":
        return "review"
    if status == "FAILED":
        return "failed"
    if status == "STOPPED":
        return "cancelled"
    return "running"


def _batch_job_phase(job: Any, default_event_type: str) -> str:
    """Resolve a product's actual lane even while prepare and execute overlap."""

    status = _text(getattr(job, "status", ""), 120).upper()
    if status in _BATCH_EXECUTE_ACTIVE or status == "DONE":
        return "batch_execute"

    run_dir = str(getattr(job, "run_dir", "") or "").strip()
    execute_log = Path(run_dir).parent / "diagnostics" / "execute.log" if run_dir else None
    if status in {"FAILED", "REVIEW", "STOPPED"} and (
        int(getattr(job, "progress", 0) or 0) >= 82
        or bool(str(getattr(job, "execution_report", "") or "").strip())
        or bool(execute_log is not None and execute_log.is_file())
    ):
        return "batch_execute"
    return default_event_type


def _read_json_file(path: str | Path | None) -> dict[str, Any]:
    if not str(path or "").strip():
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _batch_process_log(job: Any, event_type: str) -> str:
    run_dir = str(getattr(job, "run_dir", "") or "").strip()
    if not run_dir:
        return ""
    diagnostic_root = Path(run_dir).parent / "diagnostics"
    if event_type == "batch_execute":
        return str(diagnostic_root / "execute.log")
    candidates = [diagnostic_root / "prepare.log", diagnostic_root / "source.log"]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return str(candidates[0])
    return str(max(existing, key=lambda path: path.stat().st_mtime_ns))


class UsageTelemetryController(QObject):
    """Licensed-install telemetry plus owner-visible product-level task audit.

    Each supplier product gets its own audit identity. A failed Batch link therefore
    carries its own process log, traceback and executor evidence instead of being
    hidden inside one aggregate Batch payload. Authentication secrets, API keys,
    cookies and raw uploaded file binaries are never included.
    """

    def __init__(self, window: QWidget, access: ApplicationAccessController) -> None:
        super().__init__(window)
        self.window = window
        self.access = access
        self.session_id = str(uuid.uuid4())
        self.network = QNetworkAccessManager(self)
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(_HEARTBEAT_MS)
        self.heartbeat.timeout.connect(self._heartbeat)

        self._prepare_active = False
        self._execute_active = False
        self._single_audit_id = ""
        self._single_started_at = ""
        self._single_input: dict[str, Any] = {}
        self._single_result: dict[str, Any] = {}

        self._batch_id = ""
        self._batch_event_type = ""
        self._batch_job_ids: tuple[str, ...] = ()
        self._batch_audit_ids: dict[str, str] = {}
        self._batch_started_at: dict[str, str] = {}
        self._batch_inputs: dict[str, dict[str, Any]] = {}
        self._batch_flush = QTimer(self)
        self._batch_flush.setSingleShot(True)
        self._batch_flush.setInterval(_AUDIT_FLUSH_MS)
        self._batch_flush.timeout.connect(self._flush_batch_audits)

        if not self._enabled():
            return

        self._post("session_start")
        self.heartbeat.start()
        QApplication.instance().aboutToQuit.connect(self._session_end)
        self._bind_single()
        self._bind_batch()

    def _enabled(self) -> bool:
        session = self.access.session
        return bool(
            session.enforced
            and session.user_id
            and session.device_id
            and session.telemetry_token
        )

    def _base_payload(self, action: str) -> dict[str, str]:
        session = self.access.session
        return {
            "action": action,
            "user_id": session.user_id,
            "device_id": session.device_id,
            "session_id": self.session_id,
            "telemetry_token": session.telemetry_token,
            "app_version": self.access.installed_version,
        }

    def _post(
        self,
        action: str,
        *,
        event_type: str = "",
        outcome: str = "",
        audit: dict[str, Any] | None = None,
    ) -> None:
        if not self._enabled():
            return
        payload: dict[str, Any] = self._base_payload(action)
        if action == "event":
            payload["event_type"] = event_type
            payload["outcome"] = outcome
        elif action == "task_audit" and audit is not None:
            payload["audit"] = _safe_value(audit, max_text=_MAX_AUDIT_TEXT)

        request = QNetworkRequest(QUrl(self.access.telemetry_function_url))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        reply = self.network.post(
            request,
            QByteArray(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
        )
        reply.finished.connect(reply.deleteLater)

    def _event(self, event_type: str, outcome: str) -> None:
        self._post("event", event_type=event_type, outcome=outcome)

    def _task_audit(
        self,
        audit_id: str,
        *,
        task_kind: str,
        phase: str,
        status: str,
        product_url: str = "",
        input_data: dict[str, Any] | None = None,
        result_data: dict[str, Any] | None = None,
        error_text: str = "",
        started_at: str = "",
        completed_at: str = "",
    ) -> None:
        if not audit_id:
            return
        self._post(
            "task_audit",
            audit={
                "id": audit_id,
                "task_kind": task_kind,
                "phase": phase,
                "status": status,
                "product_url": _text(product_url, 4_096),
                "input_data": input_data or {},
                "result_data": result_data or {},
                "error_text": _text(error_text, 12_000),
                "started_at": started_at,
                "completed_at": completed_at,
            },
        )

    def _heartbeat(self) -> None:
        self._post("heartbeat")

    def _session_end(self) -> None:
        self.heartbeat.stop()
        self._batch_flush.stop()
        self._post("session_end")

    def _single_input_snapshot(self) -> dict[str, Any]:
        window = self.window
        vertical = getattr(window, "vertical_input", None)
        vertical_origin = ""
        try:
            vertical_origin = _text(vertical.property("listingVerticalOrigin"), 80) if vertical is not None else ""
        except Exception:
            vertical_origin = ""

        scope = getattr(window, "real_scope_combo", None)
        scope_value = ""
        current_data = getattr(scope, "currentData", None)
        if callable(current_data):
            try:
                scope_value = _text(current_data(), 160)
            except Exception:
                pass

        config = getattr(getattr(window, "runner", None), "config", None)
        model_config = {}
        for name in ("provider", "base_url", "model", "fact_model", "web_search_model", "structured_mode"):
            value = getattr(config, name, None) if config is not None else None
            if value not in (None, ""):
                model_config[name] = _safe_value(value)

        customer_files = _file_metadata(getattr(window, "_selected_product_files", ()) or ())
        return {
            "supplier_url": _widget_text(getattr(window, "url_input", None)),
            "listing_intent": _widget_text(getattr(window, "listing_intent_input", None)),
            "ai_guidance": _widget_text(getattr(window, "ai_guidance_input", None)),
            "model_name_keywords": _widget_text(getattr(window, "model_name_keywords_input", None)),
            "requested_vertical": _widget_text(vertical),
            "requested_vertical_origin": vertical_origin,
            "execution_scope": scope_value,
            "customer_files": customer_files,
            "customer_files_capture": {
                "source": "gui_snapshot",
                "count": len(customer_files),
                "state": "captured_nonempty" if customer_files else "empty_snapshot",
            },
            "model_config": model_config,
        }

    def _batch_items_snapshot(self) -> list[dict[str, Any]]:
        workspace = getattr(self.window, "batch_workspace", None)
        editor = getattr(workspace, "_batch_url_editor", None)
        rows = list(getattr(editor, "rows", ()) or ())
        items: list[dict[str, Any]] = []
        for index, row in enumerate(rows[:120], start=1):
            try:
                enabled = bool(row.is_enabled())
            except Exception:
                enabled = True
            url_getter = getattr(row, "url", None)
            try:
                url = _text(url_getter() if callable(url_getter) else "", 4_096)
            except Exception:
                url = ""
            if not url:
                continue
            customer_files = _file_metadata(getattr(row, "product_files", ()) or ())
            items.append(
                {
                    "row": index,
                    "enabled": enabled,
                    "supplier_url": url,
                    "listing_intent": _widget_text(getattr(row, "offer_input", None)),
                    "customer_files": customer_files,
                    "customer_files_capture": {
                        "source": "batch_row_snapshot",
                        "count": len(customer_files),
                        "state": "captured_nonempty" if customer_files else "empty_snapshot",
                    },
                }
            )
        return items

    def _batch_input_for_job(
        self,
        batch: Any,
        job: Any,
        index: int,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        product_url = _text(getattr(job, "product_url", ""), 4_096)
        item = next(
            (candidate for candidate in items if _text(candidate.get("supplier_url"), 4_096) == product_url),
            items[index] if index < len(items) else {},
        )
        jobs = _batch_jobs(batch)
        customer_files = _safe_value(item.get("customer_files") or [])
        return {
            "audit_scope": "batch_link",
            "batch_id": _text(getattr(batch, "batch_id", ""), 200),
            "job_id": _text(getattr(job, "job_id", ""), 160),
            "batch_index": index + 1,
            "batch_size": len(jobs),
            "supplier_url": product_url,
            "listing_intent": _text(item.get("listing_intent"), 12_000),
            "customer_files": customer_files,
            "customer_files_capture": _safe_value(
                item.get("customer_files_capture")
                or {
                    "source": "batch_row_snapshot",
                    "count": len(customer_files),
                    "state": "captured_nonempty" if customer_files else "empty_snapshot",
                }
            ),
        }

    def _reset_batch_audits(self, batch_id: str) -> None:
        self._batch_id = batch_id
        self._batch_audit_ids = {}
        self._batch_started_at = {}
        self._batch_inputs = {}

    def _ensure_batch_audits(self, batch: Any, job_ids: tuple[str, ...]) -> None:
        batch_id = _text(getattr(batch, "batch_id", ""), 200)
        if batch_id != self._batch_id:
            self._reset_batch_audits(batch_id)
        items = self._batch_items_snapshot()
        wanted = set(job_ids)
        for index, job in enumerate(_batch_jobs(batch)):
            job_id = _text(getattr(job, "job_id", ""), 160)
            if not job_id or job_id not in wanted:
                continue
            if job_id not in self._batch_audit_ids:
                self._batch_audit_ids[job_id] = str(uuid.uuid4())
                self._batch_started_at[job_id] = _utc_now()
                self._batch_inputs[job_id] = self._batch_input_for_job(batch, job, index, items)

    def _batch_job_result(
        self,
        batch: Any,
        job: Any,
        event_type: str,
        *,
        include_failure_diagnostic: bool,
    ) -> dict[str, Any]:
        job_id = _text(getattr(job, "job_id", ""), 160)
        job_status = _text(getattr(job, "status", ""), 120).upper()
        job_error = _text(getattr(job, "error", ""), 12_000)
        batch_id = _text(getattr(batch, "batch_id", ""), 200)
        jobs = _batch_jobs(batch)
        index = next((i for i, candidate in enumerate(jobs) if _text(getattr(candidate, "job_id", ""), 160) == job_id), -1)
        run_dir = _text(getattr(job, "run_dir", ""), 4_096)
        result: dict[str, Any] = {
            "audit_scope": "batch_link",
            "batch_id": batch_id,
            "job_id": job_id,
            "batch_index": index + 1 if index >= 0 else 0,
            "batch_size": len(jobs),
            "job_status": job_status,
            "product_url": _text(getattr(job, "product_url", ""), 4_096),
            "product_name": _text(getattr(job, "product_name", ""), 1_000),
            "progress": int(getattr(job, "progress", 0) or 0),
            "vertical": _text(getattr(job, "vertical", ""), 500),
            "brand": _text(getattr(job, "brand", ""), 500),
            "ready": int(getattr(job, "ready", 0) or 0),
            "blocked": int(getattr(job, "blocked", 0) or 0),
            "required_blocked": int(getattr(job, "required_blocked", 0) or 0),
            "product_images": int(getattr(job, "image_count", 0) or 0),
            "makro_target_id": _text(getattr(job, "makro_target_id", ""), 240),
            "stage_detail": _text(getattr(job, "stage_detail", ""), 2_000),
            "failure_stage": _text(getattr(job, "failure_stage", ""), 500),
            "exit_code": getattr(job, "exit_code", None),
            "error": job_error,
            "run_id": f"{batch_id}/{job_id}" if batch_id and job_id else job_id,
            "created_at": _text(getattr(job, "created_at", ""), 120),
            "updated_at": _text(getattr(job, "updated_at", ""), 120),
        }

        execution_report_path = str(getattr(job, "execution_report", "") or "").strip()
        execution_report = _read_json_file(execution_report_path)
        if execution_report:
            result["executor_report"] = _compact_executor_report(execution_report)

        if include_failure_diagnostic and (job_status in {"FAILED", "REVIEW", "STOPPED"} or job_error):
            job_root = Path(run_dir).parent if run_dir else None
            artifact_roots: tuple[str, ...] = ()
            if job_root is not None and event_type == "batch_execute":
                artifact_roots = (str(job_root / "real-execution"),)
            result["failure_diagnostic"] = collect_workflow_failure_diagnostic(
                run_dir or None,
                fallback_error=job_error or _text(getattr(job, "stage_detail", ""), 8_000),
                fallback_error_type="BatchJobFailure",
                fallback_stage=(
                    _text(getattr(job, "failure_stage", ""), 500)
                    or _text(getattr(job, "stage_detail", ""), 500)
                    or event_type
                ),
                workflow_mode="full",
                process_log_path=_batch_process_log(job, event_type),
                artifact_roots=artifact_roots,
            )
        selected_files = self._batch_inputs.get(job_id, {}).get("customer_files", [])
        return _with_material_usage(result, selected_files)

    def _post_batch_jobs(
        self,
        batch: Any,
        event_type: str,
        job_ids: tuple[str, ...],
        *,
        terminal: bool,
        include_failure_diagnostics: bool,
        forced_error: str = "",
    ) -> None:
        self._ensure_batch_audits(batch, job_ids)
        wanted = set(job_ids)
        now = _utc_now() if terminal else ""
        for job in _batch_jobs(batch):
            job_id = _text(getattr(job, "job_id", ""), 160)
            if job_id not in wanted:
                continue
            job_phase = _batch_job_phase(job, event_type)
            job_status = _text(getattr(job, "status", ""), 120).upper()
            status = _batch_job_status(job_phase, job_status)
            if terminal and forced_error and status == "running":
                status = "failed"
            include_diag = include_failure_diagnostics and (
                status in {"failed", "review", "cancelled"}
                or bool(_text(getattr(job, "error", ""), 12_000))
            )
            result_data = self._batch_job_result(
                batch,
                job,
                job_phase,
                include_failure_diagnostic=include_diag,
            )
            error_text = _text(getattr(job, "error", ""), 12_000) or (forced_error if status == "failed" else "")
            input_data = self._batch_inputs.get(job_id, {})
            self._task_audit(
                self._batch_audit_ids.get(job_id, ""),
                task_kind="batch",
                phase=job_phase,
                status=status,
                product_url=_text(getattr(job, "product_url", ""), 4_096),
                input_data=input_data,
                result_data=result_data,
                error_text=error_text,
                started_at=self._batch_started_at.get(job_id, ""),
                completed_at=now if terminal and status != "running" else "",
            )

    def _bind_single(self) -> None:
        prepare = getattr(self.window, "runner", None)
        if prepare is not None:
            prepare.running_changed.connect(self._on_prepare_running)
            prepare.completed.connect(self._on_prepare_completed)
            prepare.failed.connect(self._on_prepare_failed)

        execute = getattr(self.window, "execution_runner", None)
        if execute is not None:
            execute.running_changed.connect(self._on_execute_running)
            execute.completed.connect(self._on_execute_completed)
            execute.failed.connect(self._on_execute_failed)

    def _bind_batch(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        if controller is None:
            return
        controller.running_changed.connect(self._on_batch_running)
        controller.failed.connect(self._on_batch_failed)
        jobs_changed = getattr(controller, "jobs_changed", None)
        if jobs_changed is not None:
            jobs_changed.connect(self._on_batch_jobs_changed)

    def _on_prepare_running(self, running: bool) -> None:
        if running and not self._prepare_active:
            self._prepare_active = True
            self._event("listing_prepare", "started")
            self._single_audit_id = str(uuid.uuid4())
            self._single_started_at = _utc_now()
            self._single_input = self._single_input_snapshot()
            self._single_result = {}
            self._task_audit(
                self._single_audit_id,
                task_kind="single",
                phase="listing_prepare",
                status="running",
                product_url=_text(self._single_input.get("supplier_url"), 4_096),
                input_data=self._single_input,
                started_at=self._single_started_at,
            )

    def _on_prepare_completed(self, *args: Any) -> None:
        if self._prepare_active:
            self._prepare_active = False
            self._event("listing_prepare", "completed")
        result = _first_result(args)
        if result is not None:
            self._single_result = _run_result_payload(
                result,
                selected_files=self._single_input.get("customer_files", []),
            )
        if self._single_audit_id:
            self._task_audit(
                self._single_audit_id,
                task_kind="single",
                phase="listing_prepare",
                status="ready",
                product_url=_text(self._single_input.get("supplier_url"), 4_096),
                input_data=self._single_input,
                result_data=self._single_result,
                started_at=self._single_started_at,
            )

    def _on_prepare_failed(self, *args: Any) -> None:
        if self._prepare_active:
            self._prepare_active = False
            self._event("listing_prepare", "failed")
        if self._single_audit_id:
            runner = getattr(self.window, "runner", None)
            self._single_result = _failure_result(
                self._single_result,
                runner=runner,
                args=args,
                fallback_stage="listing_prepare",
            )
            self._single_result = _with_material_usage(
                self._single_result,
                self._single_input.get("customer_files", []),
            )
            self._task_audit(
                self._single_audit_id,
                task_kind="single",
                phase="listing_prepare",
                status="failed",
                product_url=_text(self._single_input.get("supplier_url"), 4_096),
                input_data=self._single_input,
                result_data=self._single_result,
                error_text=_error_text(args),
                started_at=self._single_started_at,
                completed_at=_utc_now(),
            )

    def _on_execute_running(self, running: bool) -> None:
        if running and not self._execute_active:
            self._execute_active = True
            self._event("listing_execute", "started")
            if not self._single_audit_id:
                self._single_audit_id = str(uuid.uuid4())
                self._single_started_at = _utc_now()
                self._single_input = self._single_input_snapshot()
            self._single_result = _with_material_usage(
                self._single_result,
                self._single_input.get("customer_files", []),
            )
            self._task_audit(
                self._single_audit_id,
                task_kind="single",
                phase="listing_execute",
                status="running",
                product_url=_text(self._single_input.get("supplier_url"), 4_096),
                input_data=self._single_input,
                result_data=self._single_result,
                started_at=self._single_started_at,
            )

    def _on_execute_completed(self, *args: Any) -> None:
        if self._execute_active:
            self._execute_active = False
            self._event("listing_execute", "completed")
        selected_files = self._single_input.get("customer_files", [])
        result = _first_result(args)
        if result is not None:
            self._single_result = _run_result_payload(result, selected_files=selected_files)
        execution_report = _first_mapping(args)
        if execution_report is not None:
            self._single_result = dict(self._single_result)
            self._single_result["executor_report"] = _compact_executor_report(execution_report)
            self._single_result["execution_elapsed_seconds"] = float(execution_report.get("_elapsed_s") or 0.0)
        self._single_result = _with_material_usage(self._single_result, selected_files)
        if self._single_audit_id:
            self._task_audit(
                self._single_audit_id,
                task_kind="single",
                phase="listing_execute",
                status="completed",
                product_url=_text(self._single_input.get("supplier_url"), 4_096),
                input_data=self._single_input,
                result_data=self._single_result,
                started_at=self._single_started_at,
                completed_at=_utc_now(),
            )

    def _on_execute_failed(self, *args: Any) -> None:
        if self._execute_active:
            self._execute_active = False
            self._event("listing_execute", "failed")
        if self._single_audit_id:
            runner = getattr(self.window, "execution_runner", None)
            self._single_result = _failure_result(
                self._single_result,
                runner=runner,
                args=args,
                fallback_stage="listing_execute",
            )
            self._single_result = _with_material_usage(
                self._single_result,
                self._single_input.get("customer_files", []),
            )
            self._task_audit(
                self._single_audit_id,
                task_kind="single",
                phase="listing_execute",
                status="failed",
                product_url=_text(self._single_input.get("supplier_url"), 4_096),
                input_data=self._single_input,
                result_data=self._single_result,
                error_text=_error_text(args),
                started_at=self._single_started_at,
                completed_at=_utc_now(),
            )

    def _on_batch_running(self, running: bool) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        batch = getattr(controller, "batch", None)
        if batch is None:
            return
        status = str(getattr(batch, "status", "") or "").upper()

        if running:
            event_type = "batch_execute" if status == "EXECUTING" else "batch_prepare"
            if not self._batch_event_type:
                self._batch_event_type = event_type
                self._batch_job_ids = _batch_operation_job_ids(batch, event_type)
                self._event(event_type, "started")
            self._post_batch_jobs(
                batch,
                event_type,
                self._batch_job_ids,
                terminal=False,
                include_failure_diagnostics=False,
            )
            return

        if not self._batch_event_type:
            return
        event_type = self._batch_event_type
        outcome, _audit_status = _batch_terminal_semantics(
            event_type,
            batch,
            self._batch_job_ids,
        )
        self._event(event_type, outcome)
        self._post_batch_jobs(
            batch,
            event_type,
            self._batch_job_ids,
            terminal=True,
            include_failure_diagnostics=True,
        )
        self._batch_event_type = ""
        self._batch_job_ids = ()

        if event_type == "batch_execute" or not any(
            str(getattr(job, "status", "") or "").upper() == "READY"
            for job in _batch_jobs(batch)
        ):
            self._batch_id = ""
            self._batch_audit_ids = {}
            self._batch_started_at = {}
            self._batch_inputs = {}

    def _on_batch_jobs_changed(self, *_args: Any) -> None:
        if self._batch_event_type and self._batch_job_ids:
            self._batch_flush.start()

    def _flush_batch_audits(self) -> None:
        if not self._batch_event_type or not self._batch_job_ids:
            return
        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        batch = getattr(controller, "batch", None)
        if batch is None:
            return
        self._post_batch_jobs(
            batch,
            self._batch_event_type,
            self._batch_job_ids,
            terminal=False,
            include_failure_diagnostics=False,
        )

    def _on_batch_failed(self, *args: Any) -> None:
        if not self._batch_event_type:
            return
        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        batch = getattr(controller, "batch", None)
        if batch is None:
            return
        self._event(self._batch_event_type, "failed")
        self._post_batch_jobs(
            batch,
            self._batch_event_type,
            self._batch_job_ids,
            terminal=True,
            include_failure_diagnostics=True,
            forced_error=_error_text(args),
        )
        self._batch_event_type = ""
        self._batch_job_ids = ()
        self._batch_id = ""
        self._batch_audit_ids = {}
        self._batch_started_at = {}
        self._batch_inputs = {}


def install_usage_telemetry(
    window: QWidget,
    access: ApplicationAccessController,
) -> UsageTelemetryController:
    existing = getattr(window, "_usage_telemetry", None)
    if isinstance(existing, UsageTelemetryController):
        return existing
    controller = UsageTelemetryController(window, access)
    window._usage_telemetry = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["UsageTelemetryController", "install_usage_telemetry"]
