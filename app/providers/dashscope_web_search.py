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


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
            else:
                text = getattr(item, "text", None) or getattr(item, "content", None)
            if text:
                parts.append(str(text))
        return "".join(parts)
    return str(content or "")


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if not raw:
        raise JSONTaskResponseError("DashScope web search 返回空文本。")
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
        "DashScope web search 未返回可解析 JSON object。"
        + (f" response_prefix={preview!r}" if preview else "")
    )


def _search_sources(chunk: Any) -> list[WebSearchSource]:
    output = _get(chunk, "output")
    search_info = _get(output, "search_info")
    if not search_info:
        return []
    raw_results = _get(search_info, "search_results", []) or []
    sources: list[WebSearchSource] = []
    for item in raw_results:
        url = str(_get(item, "url", "") or "").strip()
        if not url:
            continue
        sources.append(
            WebSearchSource(
                index=str(_get(item, "index", "") or "").strip(),
                title=str(_get(item, "title", "") or "").strip(),
                url=url,
                site_name=str(
                    _get(item, "site_name", "")
                    or _get(item, "siteName", "")
                    or ""
                ).strip(),
            )
        )
    return sources


def _chunk_text(chunk: Any) -> str:
    output = _get(chunk, "output")
    choices = _get(output, "choices", []) or []
    if not choices:
        return ""
    message = _get(choices[0], "message")
    return _message_text(_get(message, "content", ""))


def _dedupe_sources(items: Iterable[WebSearchSource]) -> list[WebSearchSource]:
    output: list[WebSearchSource] = []
    seen: set[str] = set()
    for item in items:
        key = item.url.strip()
        if key and key not in seen:
            seen.add(key)
            output.append(item)
    return output


class DashScopeWebSearchProvider:
    """One sourced web-search call with a real wall-clock deadline."""

    name = "dashscope-qwen-web-search"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        native_base_url: str = "https://dashscope.aliyuncs.com/api/v1",
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
        self.native_base_url = native_base_url.rstrip("/")
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
            import dashscope
        except ImportError as exc:  # pragma: no cover
            raise JSONTaskTransportError("缺少 dashscope Python SDK。") from exc
        dashscope.base_http_api_url = self.native_base_url
        try:
            return dashscope.MultiModalConversation.call(**kwargs)
        except Exception as exc:
            raise JSONTaskTransportError(f"DashScope web search 调用失败：{exc}") from exc

    def _search_worker(self, prompt: str) -> WebSearchJSONResult:
        response = self._call(
            api_key=self.api_key,
            model=self.model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            enable_search=True,
            search_options={
                "search_strategy": "agent",
                "enable_source": True,
            },
            response_format={"type": "json_object"},
            stream=True,
            incremental_output=True,
            result_format="message",
        )

        text_parts: list[str] = []
        sources: list[WebSearchSource] = []
        request_id = ""
        first_output = False
        started = time.monotonic()
        for chunk in response:
            status_code = _get(chunk, "status_code")
            if status_code not in (None, 200):
                raise JSONTaskTransportError(
                    "DashScope web search 请求失败："
                    f"status={status_code}, code={_get(chunk, 'code', '')}, "
                    f"message={_get(chunk, 'message', '')}"
                )
            request_id = request_id or str(_get(chunk, "request_id", "") or "")
            sources.extend(_search_sources(chunk))
            text = _chunk_text(chunk)
            if text:
                text_parts.append(text)
                if not first_output:
                    first_output = True
                    self._progress(
                        f"Web AI first output received at {time.monotonic() - started:.1f}s"
                    )

        return WebSearchJSONResult(
            payload=_parse_json_object("".join(text_parts)),
            sources=_dedupe_sources(sources),
            request_id=request_id,
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

        threading.Thread(target=worker, name="dashscope-web-search", daemon=True).start()
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
                    f"DashScope web search wall-clock deadline exceeded: "
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
            raise JSONTaskTransportError(f"DashScope web search 调用失败：{value}") from value
