from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .commerce_outbox import DurableCommerceOutbox, QueuedCommerceRequest


_RETRY_MS = 30_000


class DurableCommerceDelivery(QObject):
    """Best-effort network lane over a lossless Commerce outbox.

    Delivery is deliberately side-effect-isolated from marketplace automation:
    callers enqueue an already-proven Commerce fact and continue. Network/auth/
    server failure only leaves the event on disk for retry; it never changes the
    Makro workflow outcome.
    """

    delivery_error = Signal(str)
    delivery_accepted = Signal(object)

    def __init__(
        self,
        network: QNetworkAccessManager,
        *,
        access: Any,
        outbox_root: str | Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.network = network
        self.access = access
        self.outbox = DurableCommerceOutbox(outbox_root)
        self._inflight: QueuedCommerceRequest | None = None
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(_RETRY_MS)
        self._retry.timeout.connect(self.drain)

    def enqueue(self, request: dict[str, Any]) -> None:
        if str(request.get("action") or "").strip() != "listing_success":
            raise ValueError("unsupported commerce action")
        listing = request.get("payload", {}).get("listing", {}) if isinstance(request.get("payload"), dict) else {}
        if not isinstance(listing, dict) or not str(listing.get("external_listing_id") or "").strip():
            raise ValueError("authoritative external_listing_id is required")
        self.outbox.enqueue(request)
        self.drain()

    def endpoint(self) -> str:
        telemetry = str(getattr(self.access, "telemetry_function_url", "") or "").strip()
        if not telemetry or "/" not in telemetry:
            return ""
        return telemetry.rsplit("/", 1)[0] + "/commerce-sync"

    def drain(self) -> None:
        if self._inflight is not None:
            return
        item = self.outbox.peek()
        if item is None:
            return
        endpoint = self.endpoint()
        if not endpoint:
            self._defer(item, "commerce endpoint unavailable")
            return

        try:
            token = str(self.access.bearer_token() or "").strip()
            api_key = str(self.access.publishable_key or "").strip()
        except Exception as exc:
            self._defer(item, f"commerce auth unavailable: {type(exc).__name__}: {exc}")
            return
        if not token or not api_key:
            self._defer(item, "commerce auth unavailable")
            return

        request = QNetworkRequest(QUrl(endpoint))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Authorization", QByteArray(f"Bearer {token}".encode("utf-8")))
        request.setRawHeader(b"apikey", QByteArray(api_key.encode("utf-8")))
        encoded = json.dumps(item.request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._inflight = item
        reply = self.network.post(request, QByteArray(encoded))
        reply.finished.connect(lambda reply=reply, item=item: self._finished(reply, item))

    def _finished(self, reply: QNetworkReply, item: QueuedCommerceRequest) -> None:
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
                and str(response.get("action") or "") == "listing_success"
                and str(response.get("event_id") or "") == item.event_id
            )
            if accepted:
                self.outbox.acknowledge(item, event_id=item.event_id)
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

    def _defer(self, item: QueuedCommerceRequest, error: str) -> None:
        self.outbox.mark_failure(item, error)
        self.delivery_error.emit(error)
        if not self._retry.isActive():
            self._retry.start()


__all__ = ["DurableCommerceDelivery"]
