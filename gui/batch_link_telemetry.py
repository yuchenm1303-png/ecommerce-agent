from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QWidget

from .app_access import ApplicationAccessController
from .result_loader import load_run_result
from .task_failure_diagnostics import (
    collect_workflow_failure_diagnostic,
    sanitize_telemetry_value,
)


_FLUSH_MS = 900
_MAX_TEXT = 12_000
_MAX_LIST = 180
_TERMINAL_JOB_STATES = {"READY", "DONE", "REVIEW", "FAILED", "STOPPED"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object, limit: int = _MAX_TEXT) -> str:
    return str(value or "").strip()[: max(1, int(limit))]


def _safe(value: Any, *, depth: int = 0) -> Any:
    return sanitize_telemetry_value(value, depth=depth, max_text=_MAX_TEXT, max_list=_MAX_LIST)


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
                "identity_evidence": _safe(getattr(item, "identity_evidence", []) or []),
            }
        )

    safety = getattr(result, "safety", None)
    return _safe(
        {
            "workflow_mode": _text(getattr(result, "workflow_mode", ""), 80),
            "workflow_status": _text(getattr(result, "workflow_status", ""), 120),
            "vertical": _text(getattr(result, "vertical", ""), 500),
            "brand": _text(getattr(result, "brand", ""), 500),
            "ready": int(getattr(result, "ready", 0) or 0),
            "missing": int(getattr(result, "missing", 0) or 0),
            "conflict": int(getattr(result, "conflict", 0) or 0),
            "blocked": int(getattr(result, "blocked", 0) or 0),
            "live_field_count": int(getattr(result, "live_field_count", 0) or 0),
            "cold": _phase_stats(getattr(result, "cold", None)),
            "hot": _phase_stats(getattr(result, "hot", None)),
            "plan_summary": _safe(getattr(result, "plan_summary", {}) or {}),
            "safety": {
                "writes_performed": int(getattr(safety, "writes_performed", 0) or 0),
                "save_clicked": bool(getattr(safety, "save_clicked", False)),
                "send_to_qc_clicked": bool(getattr(safety, "send_to_qc_clicked", False)),
            },
            "fields": fields,
            "web_candidates": web_candidates,
        }
    )


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _job_phase(batch_status: str, job_status: str) -> str:
    if batch_status == "EXECUTING" or job_status in {"FILLING", "UPLOADING_IMAGES", "SAVING", "VERIFYING", "DONE"}:
        return "batch_execute"
    if batch_status == "COMPLETE" and job_status in {"DONE", "REVIEW", "FAILED", "STOPPED"}:
        return "batch_execute"
    return "batch_prepare"


def _audit_status(phase: str, job_status: str) -> tuple[str, bool]:
    status = str(job_status or "").upper()
    if phase == "batch_execute":
        if status == "DONE":
            return "completed", True
        if status == "REVIEW":
            return "review", True
        if status == "FAILED":
            return "failed", True
        if status == "STOPPED":
            return "cancelled", True
        return "running", False

    if status == "READY":
        return "ready", True
    if status == "REVIEW":
        return "review", True
    if status == "FAILED":
        return "failed", True
    if status == "STOPPED":
        return "cancelled", True
    return "running", False


class BatchLinkTelemetryController(QObject):
    """Owner audit with one persistent telemetry record per supplier URL.

    A Batch is scheduling only. Each supplier link remains an independent product
    task with its own audit identity, progress, resolved fields, execution report
    and failure diagnosis. Aggregate Batch telemetry may coexist, but this
    controller is the canonical per-link audit surface.
    """

    def __init__(self, window: QWidget, access: ApplicationAccessController) -> None:
        super().__init__(window)
        self.window = window
        self.access = access
        self.session_id = str(uuid.uuid4())
        self.network = QNetworkAccessManager(self)
        self._last_signatures: dict[str, str] = {}
        self._result_cache: dict[str, tuple[str, dict[str, Any]]] = {}
        self._bound_controller: Any = None
        self._flush = QTimer(self)
        self._flush.setSingleShot(True)
        self._flush.setInterval(_FLUSH_MS)
        self._flush.timeout.connect(self._publish_all)
        self._bind_retry = QTimer(self)
        self._bind_retry.setInterval(500)
        self._bind_retry.timeout.connect(self._try_bind)
        self._try_bind()
        if self._bound_controller is None:
            self._bind_retry.start()

    def _enabled(self) -> bool:
        session = self.access.session
        return bool(session.enforced and session.user_id and session.device_id and session.telemetry_token)

    def _try_bind(self) -> None:
        if self._bound_controller is not None:
            self._bind_retry.stop()
            return
        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        if controller is None:
            return
        self._bound_controller = controller
        controller.jobs_changed.connect(self._on_jobs_changed)
        controller.running_changed.connect(self._on_running_changed)
        controller.failed.connect(self._on_controller_failed)
        self._bind_retry.stop()
        self._schedule_publish()

    def _on_jobs_changed(self, *_args: Any) -> None:
        self._schedule_publish()

    def _on_running_changed(self, running: bool) -> None:
        if running:
            self._schedule_publish()
            return
        self._flush.stop()
        self._publish_all(force_terminal=True)

    def _on_controller_failed(self, *_args: Any) -> None:
        self._flush.stop()
        self._publish_all(force_terminal=True)

    def _schedule_publish(self) -> None:
        if self._enabled():
            self._flush.start()

    def _batch(self) -> Any:
        return getattr(self._bound_controller, "batch", None)

    def _input_items(self) -> list[dict[str, Any]]:
        workspace = getattr(self.window, "batch_workspace", None)
        editor = getattr(workspace, "_batch_url_editor", None)
        rows = list(getattr(editor, "rows", ()) or ())
        items: list[dict[str, Any]] = []
        for index, row in enumerate(rows[:120], start=1):
            try:
                enabled = bool(row.is_enabled())
            except Exception:
                enabled = True
            getter = getattr(row, "url", None)
            try:
                url = _text(getter() if callable(getter) else "", 4_096)
            except Exception:
                url = ""
            if not url and not enabled:
                continue
            files: list[dict[str, Any]] = []
            for raw in list(getattr(row, "product_files", ()) or ())[:100]:
                try:
                    path = Path(raw).expanduser().resolve()
                    size = path.stat().st_size if path.exists() else 0
                except Exception:
                    continue
                files.append({"name": path.name, "extension": path.suffix.casefold(), "size_bytes": int(size)})
            offer = getattr(row, "offer_input", None)
            offer_getter = getattr(offer, "text", None)
            try:
                listing_intent = _text(offer_getter() if callable(offer_getter) else "", 8_000)
            except Exception:
                listing_intent = ""
            items.append(
                {
                    "row": index,
                    "enabled": enabled,
                    "supplier_url": url,
                    "listing_intent": listing_intent,
                    "customer_files": files,
                }
            )
        return items

    def _model_config(self) -> dict[str, Any]:
        config = getattr(self._bound_controller, "config", None)
        output: dict[str, Any] = {}
        for name in ("provider", "base_url", "local_model", "fact_model", "web_model"):
            value = getattr(config, name, None) if config is not None else None
            if value not in (None, ""):
                output[name] = _safe(value)
        return output

    def _job_input(self, job: Any, index: int, total: int, items: list[dict[str, Any]], batch_id: str) -> dict[str, Any]:
        product_url = _text(getattr(job, "product_url", ""), 4_096)
        item = next((candidate for candidate in items if _text(candidate.get("supplier_url"), 4_096) == product_url), None)
        if item is None and 0 <= index - 1 < len(items):
            item = items[index - 1]
        item = dict(item or {})
        return _safe(
            {
                "audit_scope": "batch_link",
                "batch_id": batch_id,
                "job_id": _text(getattr(job, "job_id", ""), 160),
                "batch_index": index,
                "batch_size": total,
                "supplier_url": product_url,
                "listing_intent": item.get("listing_intent") or "",
                "customer_files": item.get("customer_files") or [],
                "model_config": self._model_config(),
            }
        )

    def _job_result(self, job: Any, index: int, total: int, batch_id: str, *, include_failure: bool) -> dict[str, Any]:
        job_status = _text(getattr(job, "status", ""), 120).upper()
        run_dir = _text(getattr(job, "run_dir", ""), 8_000)
        execution_report_path = _text(getattr(job, "execution_report", ""), 8_000)
        cache_key = "|".join(
            (
                job_status,
                _text(getattr(job, "updated_at", ""), 120),
                execution_report_path,
                "failure" if include_failure else "normal",
            )
        )
        job_id = _text(getattr(job, "job_id", ""), 160)
        cached = self._result_cache.get(job_id)
        if cached and cached[0] == cache_key:
            return cached[1]

        result: dict[str, Any] = {
            "audit_scope": "batch_link",
            "batch_id": batch_id,
            "job_id": job_id,
            "batch_index": index,
            "batch_size": total,
            "job_status": job_status,
            "progress": int(getattr(job, "progress", 0) or 0),
            "product_url": _text(getattr(job, "product_url", ""), 4_096),
            "product_name": _text(getattr(job, "product_name", ""), 1_000),
            "vertical": _text(getattr(job, "vertical", ""), 500),
            "brand": _text(getattr(job, "brand", ""), 500),
            "ready": int(getattr(job, "ready", 0) or 0),
            "blocked": int(getattr(job, "blocked", 0) or 0),
            "required_blocked": int(getattr(job, "required_blocked", 0) or 0),
            "product_images": int(getattr(job, "image_count", 0) or 0),
            "makro_target_id": _text(getattr(job, "makro_target_id", ""), 240),
            "stage_detail": _text(getattr(job, "stage_detail", ""), 4_000),
            "error": _text(getattr(job, "error", ""), 12_000),
            "run_id": Path(run_dir).name if run_dir else "",
            "created_at": _text(getattr(job, "created_at", ""), 120),
            "updated_at": _text(getattr(job, "updated_at", ""), 120),
        }

        if run_dir and job_status in _TERMINAL_JOB_STATES:
            try:
                full_result = load_run_result(Path(run_dir))
            except Exception:
                full_result = None
            if full_result is not None:
                result.update(_run_result_payload(full_result))
                result.update(
                    {
                        "audit_scope": "batch_link",
                        "batch_id": batch_id,
                        "job_id": job_id,
                        "batch_index": index,
                        "batch_size": total,
                        "job_status": job_status,
                        "progress": int(getattr(job, "progress", 0) or 0),
                        "product_name": _text(getattr(job, "product_name", ""), 1_000),
                        "makro_target_id": _text(getattr(job, "makro_target_id", ""), 240),
                        "stage_detail": _text(getattr(job, "stage_detail", ""), 4_000),
                        "error": _text(getattr(job, "error", ""), 12_000),
                        "required_blocked": int(getattr(job, "required_blocked", 0) or 0),
                        "product_images": int(getattr(job, "image_count", 0) or 0),
                        "run_id": Path(run_dir).name,
                    }
                )

        if execution_report_path:
            execution_report = _read_json(execution_report_path)
            if execution_report:
                result["executor_report"] = _safe(execution_report)
                result["execution_report_file"] = Path(execution_report_path).name

        if include_failure and (job_status in {"REVIEW", "FAILED", "STOPPED"} or result.get("error")):
            result["failure_diagnostic"] = collect_workflow_failure_diagnostic(
                run_dir or None,
                fallback_error=_text(result.get("error") or result.get("stage_detail"), 12_000),
                fallback_error_type="BatchJobFailure",
                fallback_stage=_text(result.get("stage_detail") or "batch_job", 240),
                workflow_mode="full",
            )

        safe_result = _safe(result)
        self._result_cache[job_id] = (cache_key, safe_result)
        return safe_result

    def _audit_id(self, batch_id: str, job_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"listing-studio://batch-link/{batch_id}/{job_id}"))

    def _signature(self, audit: dict[str, Any]) -> str:
        return json.dumps(_safe(audit), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _publish_all(self, force_terminal: bool = False) -> None:
        if not self._enabled():
            return
        batch = self._batch()
        jobs = list(getattr(batch, "jobs", ()) or ()) if batch is not None else []
        if not jobs:
            return
        batch_id = _text(getattr(batch, "batch_id", ""), 240)
        batch_status = _text(getattr(batch, "status", ""), 120).upper()
        items = self._input_items()
        total = len(jobs)
        for index, job in enumerate(jobs, start=1):
            job_status = _text(getattr(job, "status", ""), 120).upper()
            phase = _job_phase(batch_status, job_status)
            status, terminal = _audit_status(phase, job_status)
            include_failure = terminal and status in {"review", "failed", "cancelled"}
            input_data = self._job_input(job, index, total, items, batch_id)
            result_data = self._job_result(job, index, total, batch_id, include_failure=include_failure)
            audit = {
                "id": self._audit_id(batch_id, _text(getattr(job, "job_id", ""), 160)),
                "task_kind": "batch",
                "phase": phase,
                "status": status,
                "product_url": _text(getattr(job, "product_url", ""), 4_096),
                "input_data": input_data,
                "result_data": result_data,
                "error_text": _text(getattr(job, "error", ""), 12_000),
                "started_at": _text(getattr(job, "created_at", ""), 120) or _utc_now(),
                "completed_at": _text(getattr(job, "updated_at", ""), 120) if terminal else "",
            }
            signature = self._signature(audit)
            audit_id = str(audit["id"])
            if not force_terminal and self._last_signatures.get(audit_id) == signature:
                continue
            self._last_signatures[audit_id] = signature
            self._post_audit(audit)

    def _post_audit(self, audit: dict[str, Any]) -> None:
        session = self.access.session
        payload = {
            "action": "task_audit",
            "user_id": session.user_id,
            "device_id": session.device_id,
            "session_id": self.session_id,
            "telemetry_token": session.telemetry_token,
            "app_version": self.access.installed_version,
            "audit": _safe(audit),
        }
        request = QNetworkRequest(QUrl(self.access.telemetry_function_url))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        reply = self.network.post(
            request,
            QByteArray(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
        )
        reply.finished.connect(reply.deleteLater)


def install_batch_link_telemetry(
    window: QWidget,
    access: ApplicationAccessController,
) -> BatchLinkTelemetryController:
    existing = getattr(window, "_batch_link_telemetry", None)
    if isinstance(existing, BatchLinkTelemetryController):
        return existing
    controller = BatchLinkTelemetryController(window, access)
    window._batch_link_telemetry = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["BatchLinkTelemetryController", "install_batch_link_telemetry"]
