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
_MAX_LIST = 180
_BATCH_FAILURE_DIAGNOSTIC_LIMIT = 8


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object, limit: int = _MAX_TEXT) -> str:
    return str(value or "").strip()[: max(1, int(limit))]


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    return sanitize_telemetry_value(
        value,
        depth=depth,
        max_text=_MAX_TEXT,
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


def _file_metadata(values: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in list(values or ())[:100]:
        try:
            path = Path(raw).expanduser().resolve()
        except Exception:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        output.append(
            {
                "name": path.name,
                "extension": path.suffix.casefold(),
                "size_bytes": int(size),
            }
        )
    return output


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


def _run_result_payload(result: Any) -> dict[str, Any]:
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
        "executor_report": _safe_value(getattr(result, "executor_report", {}) or {}),
        "run_id": Path(getattr(result, "run_dir")).name,
    }
    return _safe_value(payload)


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


def _runner_run_dir(runner: Any) -> str:
    for name in ("run_dir", "output_dir", "last_run_dir"):
        value = getattr(runner, name, None) if runner is not None else None
        if str(value or "").strip():
            return str(value)
    return ""


def _failure_result(
    result_data: dict[str, Any],
    *,
    runner: Any,
    args: tuple[Any, ...],
    fallback_stage: str,
) -> dict[str, Any]:
    output = dict(result_data)
    output["failure_diagnostic"] = collect_workflow_failure_diagnostic(
        _runner_run_dir(runner),
        fallback_error=_error_text(args),
        fallback_error_type=_error_type(args),
        fallback_stage=fallback_stage,
        workflow_mode=_text(getattr(runner, "mode", ""), 120),
    )
    return _safe_value(output)


def _compact_batch_diagnostic(diagnostic: dict[str, Any]) -> dict[str, Any]:
    compact = dict(diagnostic)
    compact["timeline"] = list(compact.get("timeline") or [])[-24:]
    compact["traceback"] = _text(compact.get("traceback"), 12_000)
    return _safe_value(compact)


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
    """Return event outcome and audit status from the jobs that actually ran.

    BatchController deliberately uses PREPARED/COMPLETE to mean that its queues
    have drained. Those controller states do not mean every product succeeded.
    Telemetry therefore evaluates the exact operation cohort: all jobs for
    prepare, and only the READY jobs captured when execute started.
    """

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


class UsageTelemetryController(QObject):
    """Licensed-install telemetry plus owner-visible business task audit.

    The audit intentionally captures customer-entered listing inputs and resolved
    outputs so the owner console can inspect real usage. Authentication secrets,
    API keys, cookies and raw uploaded file binaries are never included.
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
        self._batch_event_type = ""
        self._batch_job_ids: tuple[str, ...] = ()
        self._single_audit_id = ""
        self._single_started_at = ""
        self._single_input: dict[str, Any] = {}
        self._single_result: dict[str, Any] = {}
        self._batch_audit_id = ""
        self._batch_started_at = ""
        self._batch_input: dict[str, Any] = {}
        self._batch_flush = QTimer(self)
        self._batch_flush.setSingleShot(True)
        self._batch_flush.setInterval(_AUDIT_FLUSH_MS)
        self._batch_flush.timeout.connect(self._flush_batch_audit)

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
            payload["audit"] = _safe_value(audit)

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

        return {
            "supplier_url": _widget_text(getattr(window, "url_input", None)),
            "listing_intent": _widget_text(getattr(window, "listing_intent_input", None)),
            "ai_guidance": _widget_text(getattr(window, "ai_guidance_input", None)),
            "model_name_keywords": _widget_text(getattr(window, "model_name_keywords_input", None)),
            "requested_vertical": _widget_text(vertical),
            "requested_vertical_origin": vertical_origin,
            "execution_scope": scope_value,
            "customer_files": _file_metadata(getattr(window, "_selected_product_files", ()) or ()),
            "model_config": model_config,
        }

    def _batch_input_snapshot(self) -> dict[str, Any]:
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
            if not url and not enabled:
                continue
            items.append(
                {
                    "row": index,
                    "enabled": enabled,
                    "supplier_url": url,
                    "listing_intent": _widget_text(getattr(row, "offer_input", None)),
                    "customer_files": _file_metadata(getattr(row, "product_files", ()) or ()),
                }
            )
        return {"items": items, "item_count": len(items)}

    def _batch_result_snapshot(self, *, include_failure_diagnostics: bool = False) -> dict[str, Any]:
        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        batch = getattr(controller, "batch", None)
        jobs = list(getattr(batch, "jobs", ()) or getattr(workspace, "_jobs", ()) or ())
        result_jobs: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}
        failure_diagnostics: list[dict[str, Any]] = []
        for job in jobs[:120]:
            run_dir = getattr(job, "run_dir", "")
            job_status = _text(getattr(job, "status", ""), 120).upper()
            job_error = _text(getattr(job, "error", ""), 8_000)
            status_counts[job_status] = status_counts.get(job_status, 0) + 1
            job_id = _text(getattr(job, "job_id", ""), 160)
            result_jobs.append(
                {
                    "job_id": job_id,
                    "product_url": _text(getattr(job, "product_url", ""), 4_096),
                    "status": job_status,
                    "progress": int(getattr(job, "progress", 0) or 0),
                    "vertical": _text(getattr(job, "vertical", ""), 500),
                    "brand": _text(getattr(job, "brand", ""), 500),
                    "ready": int(getattr(job, "ready", 0) or 0),
                    "blocked": int(getattr(job, "blocked", 0) or 0),
                    "required_blocked": int(getattr(job, "required_blocked", 0) or 0),
                    "product_images": int(getattr(job, "product_images", 0) or 0),
                    "makro_target_id": _text(getattr(job, "makro_target_id", ""), 240),
                    "stage_detail": _text(getattr(job, "stage_detail", ""), 2_000),
                    "error": job_error,
                    "run_id": Path(str(run_dir)).name if str(run_dir).strip() else "",
                    "created_at": _text(getattr(job, "created_at", ""), 120),
                    "updated_at": _text(getattr(job, "updated_at", ""), 120),
                }
            )
            if (
                include_failure_diagnostics
                and len(failure_diagnostics) < _BATCH_FAILURE_DIAGNOSTIC_LIMIT
                and (job_status in {"FAILED", "REVIEW", "STOPPED"} or job_error)
            ):
                diagnostic = collect_workflow_failure_diagnostic(
                    str(run_dir) if str(run_dir).strip() else None,
                    fallback_error=job_error or _text(getattr(job, "stage_detail", ""), 8_000),
                    fallback_error_type="BatchJobFailure",
                    fallback_stage="batch_job",
                    workflow_mode="full",
                )
                diagnostic = _compact_batch_diagnostic(diagnostic)
                diagnostic["job_id"] = job_id
                diagnostic["job_status"] = job_status
                failure_diagnostics.append(diagnostic)
        payload: dict[str, Any] = {
            "batch_status": _text(getattr(batch, "status", ""), 120),
            "jobs": result_jobs,
            "job_count": len(result_jobs),
            "job_status_counts": status_counts,
        }
        if failure_diagnostics:
            payload["failure_diagnostics"] = failure_diagnostics
        return _safe_value(payload)

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
            self._single_result = _run_result_payload(result)
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
        result = _first_result(args)
        if result is not None:
            self._single_result = _run_result_payload(result)
        execution_report = _first_mapping(args)
        if execution_report is not None:
            self._single_result = dict(self._single_result)
            self._single_result["executor_report"] = _safe_value(execution_report)
            self._single_result["execution_elapsed_seconds"] = float(execution_report.get("_elapsed_s") or 0.0)
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
        status = str(getattr(batch, "status", "") or "").upper()

        if running:
            event_type = "batch_execute" if status == "EXECUTING" else "batch_prepare"
            if not self._batch_event_type:
                self._batch_event_type = event_type
                self._batch_job_ids = _batch_operation_job_ids(batch, event_type)
                self._event(event_type, "started")
            if not self._batch_audit_id:
                self._batch_audit_id = str(uuid.uuid4())
                self._batch_started_at = _utc_now()
                self._batch_input = self._batch_input_snapshot()
            self._task_audit(
                self._batch_audit_id,
                task_kind="batch",
                phase=event_type,
                status="running",
                input_data=self._batch_input,
                result_data=self._batch_result_snapshot(),
                started_at=self._batch_started_at,
            )
            return

        if not self._batch_event_type:
            return
        event_type = self._batch_event_type
        outcome, audit_status = _batch_terminal_semantics(
            event_type,
            batch,
            self._batch_job_ids,
        )
        self._event(event_type, outcome)
        ready_for_execute = event_type == "batch_prepare" and any(
            str(getattr(job, "status", "") or "").upper() == "READY"
            for job in _batch_jobs(batch)
        )
        terminal_audit = event_type == "batch_execute" or not ready_for_execute
        if self._batch_audit_id:
            self._task_audit(
                self._batch_audit_id,
                task_kind="batch",
                phase=event_type,
                status=audit_status,
                input_data=self._batch_input,
                result_data=self._batch_result_snapshot(
                    include_failure_diagnostics=audit_status in {"failed", "review"}
                ),
                started_at=self._batch_started_at,
                completed_at=_utc_now() if terminal_audit else "",
            )
        self._batch_event_type = ""
        self._batch_job_ids = ()
        if terminal_audit:
            self._batch_audit_id = ""
            self._batch_started_at = ""
            self._batch_input = {}

    def _on_batch_jobs_changed(self, *_args: Any) -> None:
        if self._batch_audit_id:
            self._batch_flush.start()

    def _flush_batch_audit(self) -> None:
        if not self._batch_audit_id:
            return
        self._task_audit(
            self._batch_audit_id,
            task_kind="batch",
            phase=self._batch_event_type or "batch",
            status="running",
            input_data=self._batch_input,
            result_data=self._batch_result_snapshot(),
            started_at=self._batch_started_at,
        )

    def _on_batch_failed(self, *args: Any) -> None:
        if self._batch_event_type:
            self._event(self._batch_event_type, "failed")
        if self._batch_audit_id:
            self._task_audit(
                self._batch_audit_id,
                task_kind="batch",
                phase=self._batch_event_type or "batch",
                status="failed",
                input_data=self._batch_input,
                result_data=self._batch_result_snapshot(include_failure_diagnostics=True),
                error_text=_error_text(args),
                started_at=self._batch_started_at,
                completed_at=_utc_now(),
            )
        self._batch_event_type = ""
        self._batch_job_ids = ()
        self._batch_audit_id = ""
        self._batch_started_at = ""
        self._batch_input = {}


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
