from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .errors import JSONTaskResponseError, JSONTaskTransportError


_PROGRESS_INTERVAL_SECONDS = 15.0


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


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if not raw:
        raise JSONTaskResponseError("DashScope Responses web search 返回空文本。")
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
        "DashScope Responses web search 未返回可解析 JSON object。"
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


def _response_sources(response: Any) -> list[WebSearchSource]:
    sources: list[WebSearchSource] = []
    index = 0
    for item in _get(response, "output", []) or []:
        if str(_get(item, "type", "")) != "web_search_call":
            continue
        action = _get(item, "action") or {}
        for raw in _get(action, "sources", []) or []:
            url = str(_get(raw, "url", "") or "").strip()
            if not url:
                continue
            index += 1
            sources.append(
                WebSearchSource(
                    index=str(index),
                    title=str(_get(raw, "title", "") or "").strip(),
                    url=url,
                    site_name=str(_get(raw, "site_name", "") or "").strip(),
                )
            )
    return _dedupe_sources(sources)


def _dedupe_sources(items: Iterable[WebSearchSource]) -> list[WebSearchSource]:
    output: list[WebSearchSource] = []
    seen: set[str] = set()
    for item in items:
        key = item.url.strip().rstrip("/")
        if key and key not in seen:
            seen.add(key)
            output.append(item)
    return output


class DashScopeWebSearchProvider:
    """One bounded Qwen Responses API call with built-in sourced web search.

    Qwen3.6 Plus/Flash web search is supported through the OpenAI-compatible
    Responses API. We intentionally do not send response_format here because
    DashScope rejects JSON response_format together with the search tool for
    these models. The model is instructed to emit JSON text and we parse it
    locally; provenance comes only from web_search_call.action.sources.
    """

    name = "dashscope-qwen-responses-web-search"

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
            raise ValueError("DashScope API key 不能为空。")
        if not 10.0 <= float(request_timeout_seconds) <= 600.0:
            raise ValueError("request_timeout_seconds 必须在 10..600 秒。")
        self.model = model.strip()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = float(request_timeout_seconds)
        self._call_fn = call_fn
        self.progress_callback: Callable[[str], None] | None = None

    def set_progress_callback(self, callback: Callable[[str], None] | None) -> None:
        self.progress_callback = callback

    def _progress(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    def _call(self, **kwargs: Any) -> Any:
        if self._call_fn is not None:
            return self._call_fn(**kwargs)
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
            return client.responses.create(**kwargs)
        except Exception as exc:
            raise JSONTaskTransportError(f"DashScope Responses web search 调用失败：{exc}") from exc

    def _search_worker(self, prompt: str) -> WebSearchJSONResult:
        started = time.monotonic()
        response = self._call(
            model=self.model,
            input=prompt,
            tools=[{"type": "web_search"}],
            tool_choice="required",
            extra_body={
                "enable_thinking": False,
                "search_options": {"forced_search": True},
            },
            store=False,
        )
        self._progress(f"Web AI response received at {time.monotonic() - started:.1f}s")
        text = _response_text(response)
        return WebSearchJSONResult(
            payload=_parse_json_object(text),
            sources=_response_sources(response),
            request_id=str(_get(response, "id", "") or ""),
        )

    def search_json(self, prompt: str) -> WebSearchJSONResult:
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

        threading.Thread(target=worker, name="dashscope-responses-web-search", daemon=True).start()
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
                    "DashScope Responses web search wall-clock deadline exceeded: "
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
            raise JSONTaskTransportError(f"DashScope Responses web search 调用失败：{value}") from value
