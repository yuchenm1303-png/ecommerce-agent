from __future__ import annotations

import json
import uuid
from typing import Any

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QApplication, QWidget

from .app_access import ApplicationAccessController


_HEARTBEAT_MS = 60_000


class UsageTelemetryController(QObject):
    """Best-effort, privacy-minimal usage telemetry for licensed installs.

    The client reports only session liveness, app version and coarse workflow
    lifecycle counters. Supplier URLs, product content, AI prompts/results,
    Makro fields and customer files never leave through this channel.
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

    def _post(self, action: str, *, event_type: str = "", outcome: str = "") -> None:
        if not self._enabled():
            return
        payload = self._base_payload(action)
        if action == "event":
            payload["event_type"] = event_type
            payload["outcome"] = outcome

        request = QNetworkRequest(QUrl(self.access.telemetry_function_url))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        reply = self.network.post(
            request,
            QByteArray(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        )
        reply.finished.connect(reply.deleteLater)

    def _event(self, event_type: str, outcome: str) -> None:
        self._post("event", event_type=event_type, outcome=outcome)

    def _heartbeat(self) -> None:
        self._post("heartbeat")

    def _session_end(self) -> None:
        self.heartbeat.stop()
        self._post("session_end")

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

    def _on_prepare_running(self, running: bool) -> None:
        if running and not self._prepare_active:
            self._prepare_active = True
            self._event("listing_prepare", "started")

    def _on_prepare_completed(self, *_args: Any) -> None:
        if self._prepare_active:
            self._prepare_active = False
            self._event("listing_prepare", "completed")

    def _on_prepare_failed(self, *_args: Any) -> None:
        if self._prepare_active:
            self._prepare_active = False
            self._event("listing_prepare", "failed")

    def _on_execute_running(self, running: bool) -> None:
        if running and not self._execute_active:
            self._execute_active = True
            self._event("listing_execute", "started")

    def _on_execute_completed(self, *_args: Any) -> None:
        if self._execute_active:
            self._execute_active = False
            self._event("listing_execute", "completed")

    def _on_execute_failed(self, *_args: Any) -> None:
        if self._execute_active:
            self._execute_active = False
            self._event("listing_execute", "failed")

    def _on_batch_running(self, running: bool) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        batch = getattr(controller, "batch", None)
        status = str(getattr(batch, "status", "") or "").upper()

        if running:
            event_type = "batch_execute" if status == "EXECUTING" else "batch_prepare"
            if not self._batch_event_type:
                self._batch_event_type = event_type
                self._event(event_type, "started")
            return

        if not self._batch_event_type:
            return
        outcome = "completed" if status in {"PREPARED", "COMPLETE"} else "failed"
        self._event(self._batch_event_type, outcome)
        self._batch_event_type = ""

    def _on_batch_failed(self, *_args: Any) -> None:
        if self._batch_event_type:
            self._event(self._batch_event_type, "failed")
            self._batch_event_type = ""


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
