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
class QueuedTelemetryRequest:
    path: Path
    request_id: str
    request: dict[str, Any]
    attempts: int
    last_error: str


class DurableTelemetryOutbox:
    """Disk-backed FIFO for terminal telemetry that must survive app/process exit.

    Authentication material is deliberately not stored here. Callers persist only
    the action-specific request body; current licensed-device/session credentials
    are injected immediately before each network attempt.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def enqueue(self, request: dict[str, Any]) -> Path:
        request_id = str(uuid.uuid4())
        stamp = time.time_ns()
        path = self.root / f"{stamp:020d}-{request_id}.json"
        payload = {
            "version": _OUTBOX_VERSION,
            "request_id": request_id,
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

    def peek(self) -> QueuedTelemetryRequest | None:
        for path in self.pending_paths():
            item = self.load(path)
            if item is not None:
                return item
        return None

    def load(self, path: str | Path) -> QueuedTelemetryRequest | None:
        target = Path(path)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        request = payload.get("request")
        if not isinstance(request, dict):
            return None
        return QueuedTelemetryRequest(
            path=target,
            request_id=str(payload.get("request_id") or target.stem),
            request=request,
            attempts=max(0, int(payload.get("attempts") or 0)),
            last_error=str(payload.get("last_error") or ""),
        )

    def acknowledge(self, path: str | Path) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    def mark_failure(self, item: QueuedTelemetryRequest, error: str) -> None:
        payload = {
            "version": _OUTBOX_VERSION,
            "request_id": item.request_id,
            "created_ns": self._created_ns(item.path),
            "attempts": item.attempts + 1,
            "last_error": str(error or "delivery failed")[:4000],
            "request": item.request,
        }
        try:
            self._atomic_write(item.path, payload)
        except OSError:
            # The original queued file remains the source of truth if even the
            # retry metadata cannot be rewritten.
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


__all__ = ["DurableTelemetryOutbox", "QueuedTelemetryRequest"]
