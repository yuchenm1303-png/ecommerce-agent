from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.35, 0.85)

_NON_RETRYABLE_MARKERS = (
    "arrearage",
    "insufficient balance",
    "insufficient_balance",
    "invalid api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "forbidden",
    "permission denied",
    "billing",
    # A provider-level wall-clock deadline is the budget for the complete logical
    # request, not one transport attempt. Retrying it would exceed the very bound
    # the caller asked us to enforce. Lower-level socket/read timeouts remain
    # retryable below.
    "wall-clock deadline exceeded",
)

_RETRYABLE_MARKERS = (
    "未返回可解析",
    "返回空文本",
    "response_format",
    "model output became abnormal",
    "partial output may be incomplete or invalid json",
    "timeout",
    "timed out",
    "deadline exceeded",
    "rate limit",
    "too many requests",
    "http 429",
    "status code: 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "status code: 500",
    "status code: 502",
    "status code: 503",
    "status code: 504",
    "temporarily unavailable",
    "temporary failure",
    "connection error",
    "connection reset",
    "connection aborted",
    "connection refused",
    "server disconnected without sending a response",
    "peer closed connection",
    "remote protocol error",
    "remoteprotocolerror",
    "apiconnectionerror",
)


def exception_text(exc: BaseException) -> str:
    """Flatten the full exception chain including concrete exception types.

    Transport libraries often expose the useful retry signal only in a nested
    exception class (for example ``APIConnectionError`` ->
    ``RemoteProtocolError``) while ``str(exc)`` is merely ``Connection error``.
    Retry classification therefore uses both type names and messages and follows
    ``__cause__`` / ``__context__`` without looping.
    """

    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        type_name = f"{type(current).__module__}.{type(current).__name__}"
        parts.append(f"{type_name}: {current}")
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, BaseException):
            current = cause
        elif isinstance(context, BaseException):
            current = context
        else:
            current = None
    return " ".join(parts).casefold()


def is_retryable_ai_error(exc: BaseException) -> bool:
    text = exception_text(exc)
    if any(marker in text for marker in _NON_RETRYABLE_MARKERS):
        return False
    return any(marker in text for marker in _RETRYABLE_MARKERS)


def run_with_transient_retry(
    call: Callable[[], T],
    *,
    progress: Callable[[str], None] | None = None,
    label: str = "AI request",
    attempts: int = _MAX_ATTEMPTS,
) -> T:
    max_attempts = max(1, int(attempts))
    last_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call()
        except Exception as exc:
            last_error = exc
            if attempt >= max_attempts or not is_retryable_ai_error(exc):
                raise
            delay = _BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)]
            if progress is not None:
                progress(
                    f"{label} transient failure; retry {attempt + 1}/{max_attempts} "
                    f"after {delay:.2f}s: {exc}"
                )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


__all__ = ["exception_text", "is_retryable_ai_error", "run_with_transient_retry"]
