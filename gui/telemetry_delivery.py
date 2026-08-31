from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .telemetry_outbox import DurableTelemetryOutbox, QueuedTelemetryRequest


_RETRY_MS = 30_000


class DurableTelemetryDelivery(QObject):
    """Acknowledged, disk-backed delivery for task audits and complete log chunks.

    The outbox stores only action data. Fresh license/device/session credentials are
    injected immediately before each request, so secrets never persist in the queue.
    A queued item is deleted only after a 2xx response whose JSON body explicitly
    contains ``accepted: true``. Network errors, 4xx/5xx, malformed responses and
    application-level rejections all remain durable and are retried later.
    """

    delivery_error = Signal(str)
    delivery_accepted = Signal(object)

    def __init__(
        self,
        network: QNetworkAccessManager,
        *,
        endpoint: Callable[[], str],
        base_payload: Callable[[str], dict[str, Any]],
        outbox_root: str | Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.network = network
        self.endpoint = endpoint
        self.base_payload = base_payload
        self.outbox = DurableTelemetryOutbox(outbox_root)
        self._inflight: QueuedTelemetryRequest | None = None
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(_RETRY_MS)
        self._retry.timeout.connect(self.drain)

    def enqueue(self, request: dict[str, Any]) -> None:
        action = str(request.get("action") or "").strip()
        if action not in {"task_audit", "task_log_chunk"}:
            raise ValueError(f"unsupported durable telemetry action={action!r}")
        self.outbox.enqueue(request)
        self.drain()

    def drain(self) -> None:
        if self._inflight is not None:
            return
        item = self.outbox.peek()
        if item is None:
            return
        action = str(item.request.get("action") or "").strip()
        endpoint = str(self.endpoint() or "").strip()
        if not endpoint:
            self._defer(item, "telemetry endpoint unavailable")
            return

        try:
            payload = dict(self.base_payload(action))
        except Exception as exc:
            self._defer(item, f"telemetry credentials unavailable: {type(exc).__name__}: {exc}")
            return
        if not payload.get("telemetry_token"):
            self._defer(item, "telemetry credentials unavailable")
            return

        payload.update({key: value for key, value in item.request.items() if key != "action"})
        payload["action"] = action
        request = QNetworkRequest(QUrl(endpoint))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._inflight = item
        reply = self.network.post(request, QByteArray(encoded))
        reply.finished.connect(lambda reply=reply, item=item: self._finished(reply, item))

    def _finished(self, reply: QNetworkReply, item: QueuedTelemetryRequest) -> None:
        try:
            body = bytes(reply.readAll()).decode("utf-8", errors="replace")
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            status_code = int(status) if status is not None else 0
            network_ok = reply.error() == QNetworkReply.NetworkError.NoError
            try:
                response = json.loads(body) if body else {}
            except ValueError:
                response = {}
            accepted = (
                network_ok
                and 200 <= status_code < 300
                and isinstance(response, dict)
                and response.get("accepted") is True
                and str(response.get("action") or "") == str(item.request.get("action") or "")
            )
            if accepted:
                self.outbox.acknowledge(item.path)
                self.delivery_accepted.emit(response)
            else:
                detail = " | ".join(
                    part for part in (
                        f"network={reply.errorString()}" if not network_ok else "",
                        f"http={status_code}" if status_code else "http=missing",
                        f"body={body[:1000]}" if body else "body=<empty>",
                    ) if part
                )
                self.outbox.mark_failure(item, detail)
                self.delivery_error.emit(detail)
        finally:
            reply.deleteLater()
            self._inflight = None

        if self.outbox.peek() is not None:
            if self._retry.isActive():
                self._retry.stop()
            self._retry.start()

    def _defer(self, item: QueuedTelemetryRequest, error: str) -> None:
        self.outbox.mark_failure(item, error)
        self.delivery_error.emit(error)
        if not self._retry.isActive():
            self._retry.start()


__all__ = ["DurableTelemetryDelivery"]
