from __future__ import annotations

from collections import deque
from collections.abc import Iterable


BATCH_LOG_MAX_LINES = 400
BATCH_LOG_MAX_LINE_CHARS = 2_000
BATCH_LOG_PREVIEW_CHARS = 190
BATCH_LOG_PENDING_LINES = 800
BATCH_LOG_FLUSH_LINES = 25


def display_log_line(value: object) -> str:
    """Return a bounded UI copy; full process output remains in diagnostics logs."""

    clean = str(value or "").strip()
    if len(clean) <= BATCH_LOG_MAX_LINE_CHARS:
        return clean
    omitted = len(clean) - BATCH_LOG_MAX_LINE_CHARS
    return f"{clean[:BATCH_LOG_MAX_LINE_CHARS]} … [UI truncated {omitted} chars]"


def log_buffer(lines: Iterable[str] = ()) -> deque[str]:
    return deque((display_log_line(line) for line in lines if str(line or "").strip()), maxlen=BATCH_LOG_MAX_LINES)


__all__ = [
    "BATCH_LOG_MAX_LINES",
    "BATCH_LOG_MAX_LINE_CHARS",
    "BATCH_LOG_PREVIEW_CHARS",
    "BATCH_LOG_PENDING_LINES",
    "BATCH_LOG_FLUSH_LINES",
    "display_log_line",
    "log_buffer",
]
