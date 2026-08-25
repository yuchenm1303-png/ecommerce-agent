"""Run-scoped AI usage telemetry with no prompt or credential capture.

Every physical SDK request is appended as one compact JSONL event. The journal
is intentionally transport-level: retries become separate physical attempts,
while all attempts from one semantic task share one logical_request_id.
"""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


USAGE_JOURNAL_ENV = "ECOM_AI_USAGE_JOURNAL"
_SCHEMA_VERSION = 1
_WRITE_LOCK = threading.Lock()


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def normalize_usage(value: Any) -> dict[str, int]:
    """Normalize Chat Completions and Responses usage objects."""

    if value is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
        }
    input_tokens = _int(_get(value, "input_tokens", _get(value, "prompt_tokens", 0)))
    output_tokens = _int(_get(value, "output_tokens", _get(value, "completion_tokens", 0)))
    total_tokens = _int(_get(value, "total_tokens", input_tokens + output_tokens))
    input_details = _get(value, "input_tokens_details", _get(value, "prompt_tokens_details", {})) or {}
    output_details = _get(value, "output_tokens_details", _get(value, "completion_tokens_details", {})) or {}
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens or input_tokens + output_tokens,
        "cached_input_tokens": _int(_get(input_details, "cached_tokens", 0)),
        "reasoning_tokens": _int(_get(output_details, "reasoning_tokens", 0)),
    }


def response_usage(response: Any) -> dict[str, int]:
    return normalize_usage(_get(response, "usage", None))


def response_web_search_calls(response: Any) -> int:
    count = 0
    for item in _get(response, "output", []) or []:
        if str(_get(item, "type", "") or "") == "web_search_call":
            count += 1
    return count


def classify_task(task: str) -> str:
    normalized = str(task or "").strip().casefold()
    if not normalized:
        return "semantic"
    if "web_search" in normalized or normalized.startswith("web_"):
        return "web"
    if "product_identity" in normalized or "supplier_product_identity" in normalized:
        return "product_identity"
    if "vertical" in normalized or "product_type_search" in normalized:
        return "vertical"
    if "brand" in normalized:
        return "brand"
    if "image" in normalized or "visual" in normalized:
        return "image_evidence"
    if "best_effort" in normalized or "inference" in normalized:
        return "best_effort_inference"
    if "product_fact" in normalized or "field_fact" in normalized or "resolve_fact" in normalized:
        return "product_facts"
    return normalized[:120]


@dataclass(slots=True)
class UsageRequestContext:
    task: str
    provider: str
    model: str
    logical_request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _attempt: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def stage(self) -> str:
        return classify_task(self.task)

    def next_attempt(self) -> int:
        with self._lock:
            self._attempt += 1
            return self._attempt


_CURRENT_CONTEXT: contextvars.ContextVar[UsageRequestContext | None] = contextvars.ContextVar(
    "ai_usage_request_context",
    default=None,
)


@contextmanager
def usage_request_context(*, task: str, provider: str, model: str) -> Iterator[UsageRequestContext]:
    context = UsageRequestContext(task=str(task or ""), provider=str(provider or ""), model=str(model or ""))
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)


def ensure_usage_journal(run_dir: str | Path) -> Path:
    """Bind this process and inherited child processes to one run journal."""

    existing = str(os.environ.get(USAGE_JOURNAL_ENV) or "").strip()
    if existing:
        return Path(existing)
    path = Path(run_dir).resolve() / "ai-usage.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.environ[USAGE_JOURNAL_ENV] = str(path)
    except Exception:
        # Telemetry must never become a production dependency.
        pass
    return path


def current_usage_journal() -> Path | None:
    raw = str(os.environ.get(USAGE_JOURNAL_ENV) or "").strip()
    return Path(raw) if raw else None


def _summary_bucket() -> dict[str, int]:
    return {
        "physical_requests": 0,
        "logical_requests": 0,
        "retry_requests": 0,
        "response_requests": 0,
        "error_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "web_search_calls": 0,
    }


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("kind") == "ai_http_request":
                events.append(item)
    except Exception:
        return []
    return events


def summarize_usage_journal(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else current_usage_journal()
    events = _read_events(target) if target is not None else []
    overall = _summary_bucket()
    logical_ids: set[str] = set()
    by_stage: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}
    stage_logical: dict[str, set[str]] = {}
    model_logical: dict[str, set[str]] = {}

    def add(bucket: dict[str, int], event: dict[str, Any]) -> None:
        bucket["physical_requests"] += 1
        bucket["response_requests" if event.get("status") == "response" else "error_requests"] += 1
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
            "web_search_calls",
        ):
            bucket[key] += _int(event.get(key))

    for event in events:
        logical_id = str(event.get("logical_request_id") or "")
        if logical_id:
            logical_ids.add(logical_id)
        add(overall, event)
        stage = str(event.get("stage") or "semantic")
        model = str(event.get("model") or "unknown")
        by_stage.setdefault(stage, _summary_bucket())
        by_model.setdefault(model, _summary_bucket())
        add(by_stage[stage], event)
        add(by_model[model], event)
        if logical_id:
            stage_logical.setdefault(stage, set()).add(logical_id)
            model_logical.setdefault(model, set()).add(logical_id)

    overall["logical_requests"] = len(logical_ids)
    overall["retry_requests"] = max(0, overall["physical_requests"] - overall["logical_requests"])
    for name, bucket in by_stage.items():
        bucket["logical_requests"] = len(stage_logical.get(name, set()))
        bucket["retry_requests"] = max(0, bucket["physical_requests"] - bucket["logical_requests"])
    for name, bucket in by_model.items():
        bucket["logical_requests"] = len(model_logical.get(name, set()))
        bucket["retry_requests"] = max(0, bucket["physical_requests"] - bucket["logical_requests"])

    return {
        "schema_version": _SCHEMA_VERSION,
        "journal": str(target.resolve()) if target is not None else "",
        **overall,
        "by_stage": by_stage,
        "by_model": by_model,
    }


def _write_summary(path: Path) -> None:
    summary = summarize_usage_journal(path)
    target = path.with_name("ai-usage-summary.json")
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)


def record_current_request(
    *,
    api_kind: str,
    model: str,
    status: str,
    started: float,
    response: Any | None = None,
    error: BaseException | None = None,
) -> None:
    """Record one physical SDK call. Never raises into production code."""

    path = current_usage_journal()
    if path is None:
        return
    context = _CURRENT_CONTEXT.get()
    if context is None:
        context = UsageRequestContext(task="", provider="semantic-provider", model=model)
    attempt = context.next_attempt()
    usage = response_usage(response)
    body = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "ai_http_request",
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "pid": os.getpid(),
        "provider": context.provider,
        "model": str(model or context.model or ""),
        "api_kind": str(api_kind or ""),
        "stage": context.stage,
        "task": context.task,
        "logical_request_id": context.logical_request_id,
        "attempt": attempt,
        "status": "response" if status == "response" else "error",
        "request_id": str(_get(response, "id", "") or ""),
        **usage,
        "web_search_calls": response_web_search_calls(response),
        "elapsed_seconds": round(max(0.0, time.monotonic() - started), 3),
        "error_type": type(error).__name__ if error is not None else "",
    }
    try:
        with _WRITE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
            _write_summary(path)
    except Exception:
        # Cost telemetry is observational only; logging failure cannot affect AI.
        return


class _UsageStream:
    def __init__(self, stream: Any, *, api_kind: str, model: str, started: float) -> None:
        self._stream = stream
        self._api_kind = api_kind
        self._model = model
        self._started = started
        self._finished = False
        self._last_response: Any | None = None

    def _finish(self, status: str, error: BaseException | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        record_current_request(
            api_kind=self._api_kind,
            model=self._model,
            status=status,
            started=self._started,
            response=self._last_response,
            error=error,
        )

    def __iter__(self):
        try:
            for chunk in self._stream:
                if _get(chunk, "usage", None) is not None or _get(chunk, "id", None):
                    self._last_response = chunk
                yield chunk
            self._finish("response")
        except BaseException as exc:
            self._finish("error", exc)
            raise

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        try:
            if callable(close):
                close()
        finally:
            if not self._finished:
                self._finish("response")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def _looks_streaming(value: Any) -> bool:
    return hasattr(value, "__iter__") and not hasattr(value, "choices") and not hasattr(value, "output")


class _CreateProxy:
    def __init__(self, delegate: Any, *, api_kind: str, default_model: str) -> None:
        self._delegate = delegate
        self._api_kind = api_kind
        self._default_model = default_model

    def create(self, *args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        model = str(kwargs.get("model") or self._default_model or "")
        try:
            response = self._delegate.create(*args, **kwargs)
        except BaseException as exc:
            record_current_request(
                api_kind=self._api_kind,
                model=model,
                status="error",
                started=started,
                error=exc,
            )
            raise
        if _looks_streaming(response):
            return _UsageStream(response, api_kind=self._api_kind, model=model, started=started)
        record_current_request(
            api_kind=self._api_kind,
            model=model,
            status="response",
            started=started,
            response=response,
        )
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class _ChatProxy:
    def __init__(self, delegate: Any, *, default_model: str) -> None:
        self._delegate = delegate
        self.completions = _CreateProxy(
            delegate.completions,
            api_kind="chat.completions",
            default_model=default_model,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class InstrumentedOpenAIClient:
    """Transparent OpenAI-client proxy that observes only create() responses."""

    _ai_usage_instrumented = True

    def __init__(self, delegate: Any, *, default_model: str) -> None:
        self._delegate = delegate
        if hasattr(delegate, "chat"):
            self.chat = _ChatProxy(delegate.chat, default_model=default_model)
        if hasattr(delegate, "responses"):
            self.responses = _CreateProxy(
                delegate.responses,
                api_kind="responses",
                default_model=default_model,
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def instrument_openai_client(client: Any, *, default_model: str) -> Any:
    if getattr(client, "_ai_usage_instrumented", False):
        return client
    try:
        return InstrumentedOpenAIClient(client, default_model=default_model)
    except Exception:
        return client


__all__ = [
    "USAGE_JOURNAL_ENV",
    "classify_task",
    "current_usage_journal",
    "ensure_usage_journal",
    "instrument_openai_client",
    "normalize_usage",
    "record_current_request",
    "summarize_usage_journal",
    "usage_request_context",
]
