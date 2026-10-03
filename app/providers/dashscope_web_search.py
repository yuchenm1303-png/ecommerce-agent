from __future__ import annotations

import contextvars
import json
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from .errors import JSONTaskResponseError, JSONTaskTransportError
from .transient_retry import run_with_transient_retry
from .usage_telemetry import instrument_openai_client, usage_request_context


_PROGRESS_INTERVAL_SECONDS = 15.0
WEB_PROTOCOL_OPENAI = "openai-responses"
WEB_PROTOCOL_DASHSCOPE = "dashscope-responses"


@dataclass(slots=True, frozen=True)
class WebSearchSource:
    index: str
    title: str
    url: str
    site_name: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "index": self.index,
            "title": self.title,
            "url": self.url,
            "site_name": self.site_name,
        }


@dataclass(slots=True)
class WebSearchJSONResult:
    payload: dict[str, Any]
    sources: list[WebSearchSource]
    request_id: str = ""
    protocol: str = ""


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if not raw:
        raise JSONTaskResponseError("Responses web search 返回空文本。")
    candidates = [raw]
    if raw.startswith("```") and raw.endswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3:
            candidates.append("\n".join(lines[1:-1]).strip())
    first, last = raw.find("{"), raw.rfind("}")
    if 0 <= first < last:
        candidates.append(raw[first : last + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    preview = raw[:300].replace("\n", " ")
    raise JSONTaskResponseError(
        "Responses web search 未返回可解析 JSON object。"
        + (f" response_prefix={preview!r}" if preview else "")
    )


def _response_text(response: Any) -> str:
    direct = _get(response, "output_text", "")
    if direct:
        return str(direct)
    parts: list[str] = []
    for item in _get(response, "output", []) or []:
        if str(_get(item, "type", "")) != "message":
            continue
        for content in _get(item, "content", []) or []:
            ctype = str(_get(content, "type", ""))
            if ctype in {"output_text", "text"}:
                text = _get(content, "text", "")
                if text:
                    parts.append(str(text))
    return "".join(parts)


def _source_url(source: Any) -> str:
    if isinstance(source, dict):
        direct = str(source.get("url") or "").strip()
        if direct:
            return direct
        citation = source.get("url_citation")
        if isinstance(citation, dict):
            return str(citation.get("url") or "").strip()
        return ""
    direct = str(getattr(source, "url", "") or "").strip()
    if direct:
        return direct
    citation = getattr(source, "url_citation", None)
    return str(getattr(citation, "url", "") or "").strip() if citation is not None else ""


def _source_title(source: Any) -> str:
    if isinstance(source, dict):
        direct = str(source.get("title") or "").strip()
        if direct:
            return direct
        citation = source.get("url_citation")
        return str(citation.get("title") or "").strip() if isinstance(citation, dict) else ""
    direct = str(getattr(source, "title", "") or "").strip()
    if direct:
        return direct
    citation = getattr(source, "url_citation", None)
    return str(getattr(citation, "title", "") or "").strip() if citation is not None else ""


def _append_source(
    output: list[WebSearchSource],
    raw: Any,
) -> None:
    url = _source_url(raw)
    if not url.startswith(("http://", "https://")):
        return
    output.append(
        WebSearchSource(
            index=str(len(output) + 1),
            title=_source_title(raw),
            url=url,
            site_name=str(_get(raw, "site_name", "") or "").strip(),
        )
    )


def response_has_web_search_call(response: Any) -> bool:
    return any(
        str(_get(item, "type", "") or "") == "web_search_call"
        for item in _get(response, "output", []) or []
    )


def response_web_sources(response: Any) -> list[WebSearchSource]:
    """Extract sources from both DashScope action.sources and OpenAI URL citations."""
    sources: list[WebSearchSource] = []
    for item in _get(response, "output", []) or []:
        item_type = str(_get(item, "type", "") or "")
        if item_type == "web_search_call":
            action = _get(item, "action") or {}
            for raw in _get(action, "sources", []) or []:
                _append_source(sources, raw)
            continue
        if item_type != "message":
            continue
        for content in _get(item, "content", []) or []:
            for annotation in _get(content, "annotations", []) or []:
                annotation_type = str(_get(annotation, "type", "") or "")
                if annotation_type in {"url_citation", "citation"}:
                    _append_source(sources, annotation)
    return _dedupe_sources(sources)


def _dedupe_sources(items: Iterable[WebSearchSource]) -> list[WebSearchSource]:
    output: list[WebSearchSource] = []
    seen: set[str] = set()
    for item in items:
        key = item.url.strip().rstrip("/")
        if key and key not in seen:
            seen.add(key)
            output.append(
                WebSearchSource(
                    index=str(len(output) + 1),
                    title=item.title,
                    url=item.url,
                    site_name=item.site_name,
                )
            )
    return output


def _protocol_order(base_url: str, preferred_protocol: str = "") -> list[str]:
    preferred = str(preferred_protocol or "").strip()
    host = (urlparse(str(base_url or "")).hostname or "").casefold()
    default = (
        [WEB_PROTOCOL_DASHSCOPE, WEB_PROTOCOL_OPENAI]
        if host.endswith("dashscope.aliyuncs.com")
        else [WEB_PROTOCOL_OPENAI, WEB_PROTOCOL_DASHSCOPE]
    )
    if preferred in {WEB_PROTOCOL_OPENAI, WEB_PROTOCOL_DASHSCOPE}:
        return [preferred, *[item for item in default if item != preferred]]
    return default


def _attempt_specs(
    *,
    protocol: str,
    model: str,
    prompt: str,
) -> list[dict[str, Any]]:
    base = {
        "model": model,
        "input": prompt,
        "tools": [{"type": "web_search"}],
    }
    if protocol == WEB_PROTOCOL_OPENAI:
        # Start with the smallest standards-compatible request. Some relays reject
        # tool_choice even though the upstream model can search. If the model ignores
        # the explicit search instruction, retry with required tool choice.
        return [
            dict(base),
            {**base, "tool_choice": "required"},
        ]
    if protocol == WEB_PROTOCOL_DASHSCOPE:
        return [
            {
                **base,
                "tool_choice": "required",
                "extra_body": {
                    "enable_thinking": False,
                    "search_options": {"forced_search": True},
                },
                "store": False,
            }
        ]
    raise ValueError(f"unsupported web protocol: {protocol!r}")


def negotiate_responses_web_search(
    client: Any,
    *,
    model: str,
    prompt: str,
    base_url: str,
    preferred_protocol: str = "",
) -> tuple[Any, str]:
    """Try standards-first or DashScope-first Responses web search and verify evidence.

    A request is considered compatible only when it emits a real ``web_search_call``
    and exposes at least one HTTP(S) source, either through ``action.sources`` or
    standard Responses ``url_citation`` annotations.
    """
    failures: list[str] = []
    for protocol in _protocol_order(base_url, preferred_protocol):
        for kwargs in _attempt_specs(protocol=protocol, model=model, prompt=prompt):
            try:
                response = client.responses.create(**kwargs)
            except Exception as exc:
                failures.append(f"{protocol}: {str(exc)[:360]}")
                continue
            if not response_has_web_search_call(response):
                failures.append(f"{protocol}: response contained no web_search_call")
                continue
            sources = response_web_sources(response)
            if not sources:
                failures.append(f"{protocol}: web_search_call contained no source URL")
                continue
            return response, protocol
    detail = " | ".join(failures[-6:]) or "no compatible Responses web-search protocol"
    raise JSONTaskTransportError("没有找到可用的 Responses web_search 协议：" + detail)


class DashScopeWebSearchProvider:
    """Compatible sourced Responses web search with protocol auto-negotiation.

    The historical class name is kept for import compatibility. DashScope endpoints
    keep their proven request shape first; other OpenAI-compatible relays try the
    standard Responses web_search shape first and fall back to DashScope extensions.
    """

    name = "compatible-responses-web-search"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        request_timeout_seconds: float = 120.0,
        call_fn: Callable[..., Any] | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("web search model 不能为空。")
        if not api_key.strip():
            raise ValueError("Web search API key 不能为空。")
        if not 10.0 <= float(request_timeout_seconds) <= 600.0:
            raise ValueError("request_timeout_seconds 必须在 10..600 秒。")
        self.model = model.strip()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = float(request_timeout_seconds)
        self._call_fn = call_fn
        self._resolved_protocol = ""
        self.progress_callback: Callable[[str], None] | None = None

    @property
    def resolved_protocol(self) -> str:
        return self._resolved_protocol

    def set_progress_callback(self, callback: Callable[[str], None] | None) -> None:
        self.progress_callback = callback

    def _progress(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    def _client(self) -> Any:
        if self._call_fn is not None:
            class _ResponsesProxy:
                def __init__(self, call_fn: Callable[..., Any]) -> None:
                    self._call_fn = call_fn

                def create(self, **kwargs: Any) -> Any:
                    return self._call_fn(**kwargs)

            class _ClientProxy:
                def __init__(self, call_fn: Callable[..., Any]) -> None:
                    self.responses = _ResponsesProxy(call_fn)

            return _ClientProxy(self._call_fn)
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise JSONTaskTransportError("缺少 openai Python SDK。") from exc
        try:
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.request_timeout_seconds,
                max_retries=0,
            )
            return instrument_openai_client(client, default_model=self.model)
        except Exception as exc:
            raise JSONTaskTransportError(f"Responses web search client 初始化失败：{exc}") from exc

    def _search_worker(self, prompt: str) -> WebSearchJSONResult:
        started = time.monotonic()
        try:
            response, protocol = negotiate_responses_web_search(
                self._client(),
                model=self.model,
                prompt=prompt,
                base_url=self.base_url,
                preferred_protocol=self._resolved_protocol,
            )
        except Exception as exc:
            if isinstance(exc, (JSONTaskTransportError, JSONTaskResponseError)):
                raise
            raise JSONTaskTransportError(f"Responses web search 调用失败：{exc}") from exc
        self._resolved_protocol = protocol
        self._progress(
            f"Web AI response received via {protocol} at {time.monotonic() - started:.1f}s"
        )
        text = _response_text(response)
        return WebSearchJSONResult(
            payload=_parse_json_object(text),
            sources=response_web_sources(response),
            request_id=str(_get(response, "id", "") or ""),
            protocol=protocol,
        )

    def _search_json_once(self, prompt: str) -> WebSearchJSONResult:
        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
        started = time.monotonic()

        def worker() -> None:
            try:
                result_queue.put_nowait(("ok", self._search_worker(prompt)))
            except BaseException as exc:
                try:
                    result_queue.put_nowait(("error", exc))
                except queue.Full:
                    pass

        copied_context = contextvars.copy_context()
        threading.Thread(
            target=lambda: copied_context.run(worker),
            name="responses-web-search",
            daemon=True,
        ).start()
        deadline = started + self.request_timeout_seconds
        next_progress = started + _PROGRESS_INTERVAL_SECONDS
        self._progress(
            f"Web AI request started; wall-clock deadline={self.request_timeout_seconds:.0f}s"
        )

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                raise JSONTaskTransportError(
                    "Responses web search wall-clock deadline exceeded: "
                    f"{self.request_timeout_seconds:.0f}s"
                )
            wait = min(remaining, max(0.05, next_progress - now))
            try:
                kind, value = result_queue.get(timeout=wait)
            except queue.Empty:
                now = time.monotonic()
                if now >= next_progress:
                    self._progress(
                        f"Web AI still running: elapsed={now - started:.1f}s / "
                        f"deadline={self.request_timeout_seconds:.0f}s"
                    )
                    next_progress = now + _PROGRESS_INTERVAL_SECONDS
                continue

            if kind == "ok":
                self._progress(f"Web AI response complete at {time.monotonic() - started:.1f}s")
                return value
            if isinstance(value, (JSONTaskTransportError, JSONTaskResponseError)):
                raise value
            raise JSONTaskTransportError(f"Responses web search 调用失败：{value}") from value

    def search_json(self, prompt: str) -> WebSearchJSONResult:
        with usage_request_context(task="web_search", provider=self.name, model=self.model):
            return run_with_transient_retry(
                lambda: self._search_json_once(prompt),
                progress=self._progress,
                label="Web AI request",
            )


CompatibleWebSearchProvider = DashScopeWebSearchProvider


__all__ = [
    "CompatibleWebSearchProvider",
    "DashScopeWebSearchProvider",
    "WEB_PROTOCOL_DASHSCOPE",
    "WEB_PROTOCOL_OPENAI",
    "WebSearchJSONResult",
    "WebSearchSource",
    "negotiate_responses_web_search",
    "response_has_web_search_call",
    "response_web_sources",
]
