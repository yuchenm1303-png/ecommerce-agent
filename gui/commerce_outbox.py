from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_OUTBOX_VERSION = 1


@dataclass(slots=True, frozen=True)
class QueuedCommerceRequest:
    path: Path
    event_id: str
    request: dict[str, Any]
    attempts: int
    last_error: str


class DurableCommerceOutbox:
    """Lossless disk-backed FIFO for authoritative Commerce facts.

    Unlike diagnostic telemetry, Commerce events have no retention cap and are
    never evicted to make room. Authentication material is never persisted. An
    event file is removed only after the server explicitly acknowledges the same
    event id.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def enqueue(self, request: dict[str, Any]) -> Path:
        event_id = str(request.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("commerce event_id must not be empty")
        canonical = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for path in self.pending_paths():
            item = self.load(path)
            if item is None or item.event_id != event_id:
                continue
            existing = json.dumps(item.request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if existing != canonical:
                raise ValueError(f"commerce event_id collision: {event_id}")
            return item.path

        stamp = time.time_ns()
        path = self.root / f"{stamp:020d}-{event_id}.json"
        payload = {
            "version": _OUTBOX_VERSION,
            "event_id": event_id,
            "created_ns": stamp,
            "attempts": 0,
            "last_error": "",
            "request": request,
        }
        self._atomic_write(path, payload)
        return path

    def pending_paths(self) -> list[Path]:
        try:
            return sorted(path for path in self.root.glob("*.json") if path.is_file())
        except OSError:
            return []

    def peek(self) -> QueuedCommerceRequest | None:
        for path in self.pending_paths():
            item = self.load(path)
            if item is not None:
                return item
        return None

    def load(self, path: str | Path) -> QueuedCommerceRequest | None:
        target = Path(path)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict) or int(payload.get("version") or 0) != _OUTBOX_VERSION:
            return None
        request = payload.get("request")
        if not isinstance(request, dict):
            return None
        event_id = str(payload.get("event_id") or request.get("event_id") or "").strip()
        if not event_id:
            return None
        return QueuedCommerceRequest(
            path=target,
            event_id=event_id,
            request=request,
            attempts=max(0, int(payload.get("attempts") or 0)),
            last_error=str(payload.get("last_error") or ""),
        )

    def acknowledge(self, item: QueuedCommerceRequest, *, event_id: str) -> None:
        if str(event_id or "").strip() != item.event_id:
            raise ValueError("commerce acknowledgement event_id mismatch")
        try:
            Path(item.path).unlink(missing_ok=True)
        except OSError:
            # Server acceptance is already authoritative. If local deletion fails,
            # retaining the file is safer than raising into the GUI; a later retry
            # is idempotent at the server ledger.
            pass

    def mark_failure(self, item: QueuedCommerceRequest, error: str) -> None:
        payload = {
            "version": _OUTBOX_VERSION,
            "event_id": item.event_id,
            "created_ns": self._created_ns(item.path),
            "attempts": item.attempts + 1,
            "last_error": str(error or "delivery failed")[:4000],
            "request": item.request,
        }
        try:
            self._atomic_write(item.path, payload)
        except OSError:
            # Failure metadata is secondary. The original durable event remains
            # the source of truth when even this rewrite cannot be completed.
            pass

    @staticmethod
    def _created_ns(path: Path) -> int:
        try:
            return int(path.name.split("-", 1)[0])
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = ["DurableCommerceOutbox", "QueuedCommerceRequest"]
