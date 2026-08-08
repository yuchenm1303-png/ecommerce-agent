from __future__ import annotations

import base64
import json
import mimetypes
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .errors import JSONTaskProviderError, JSONTaskResponseError, JSONTaskTransportError


class OpenAICompatibleProviderError(JSONTaskProviderError):
    pass


class OpenAICompatibleTransportError(JSONTaskTransportError, OpenAICompatibleProviderError):
    pass


class OpenAICompatibleResponseError(JSONTaskResponseError, OpenAICompatibleProviderError):
    pass


SUPPORTED_COMPAT_PROFILES = ("generic", "qwen-omni")
_PROGRESS_INTERVAL_SECONDS = 15.0


def _image_data_uri(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_file():
        raise OpenAICompatibleProviderError(f"OpenAI-compatible provider 找不到图片：{path}")
    mime, _ = mimetypes.guess_type(path.name)
    if not mime or not mime.startswith("image/"):
        raise OpenAICompatibleProviderError(f"无法识别图片 MIME type：{path}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _prompt_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    """Serialize only model-relevant task data; local paths/digests stay local."""

    payload = {
        "task": request_payload.get("task"),
        "product_identity": request_payload.get("product_identity") or {},
        "target_fields": request_payload.get("target_fields") or [],
        "rules": request_payload.get("rules") or [],
        "grounded_sources": [],
        "json_contract": request_payload.get("json_contract") or {},
    }
    for source in request_payload.get("grounded_sources") or []:
        if not isinstance(source, dict):
            continue
        item = {
            "source_id": source.get("source_id", ""),
            "source_type": source.get("source_type", ""),
            "kind": source.get("kind", ""),
        }
        if source.get("kind") == "text":
            item["origin"] = source.get("origin", "")
            item["content"] = source.get("content", "")
        payload["grounded_sources"].append(item)
    return payload


def _task_instruction(request_payload: dict[str, Any]) -> str:
    parts = [str(request_payload.get("prompt_instruction") or "").strip()]
    validation_error = str(request_payload.get("validation_error") or "").strip()
    if validation_error:
        parts.append(
            "CORRECTION REQUIRED: the prior JSON failed the structural contract: "
            + validation_error
        )
    parts.append(
        "Return exactly one valid JSON object and no markdown or prose outside JSON. "
        "Follow json_contract. Never invent unsupported product facts."
    )
    return "\n\n".join(part for part in parts if part)


def _message_content(request_payload: dict[str, Any], *, image_detail: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                _task_instruction(request_payload)
                + "\n\n"
                + json.dumps(_prompt_payload(request_payload), ensure_ascii=False, separators=(",", ":"))
            ),
        }
    ]
    for source in request_payload.get("grounded_sources") or []:
        if not isinstance(source, dict) or source.get("kind") != "image":
            continue
        source_id = str(source.get("source_id") or "")
        content.append(
            {
                "type": "text",
                "text": (
                    f"The immediately following image is source_id={source_id}. "
                    "Citations to it must use exactly this source_reference."
                ),
            }
        )
        image_url: dict[str, Any] = {"url": _image_data_uri(str(source.get("image_path") or ""))}
        if image_detail in {"low", "high"}:
            image_url["detail"] = image_detail
        content.append({"type": "image_url", "image_url": image_url})
    return content


def _extract_message_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise OpenAICompatibleResponseError("OpenAI-compatible API 没有返回 choices。")
    message = getattr(choices[0], "message", None)
    if message is None:
        raise OpenAICompatibleResponseError("OpenAI-compatible API choice 缺少 message。")
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _extract_stream_text(
    stream: Any,
    *,
    started: float,
    progress: Callable[[str], None] | None = None,
) -> str:
    parts: list[str] = []
    first_output_reported = False
    for chunk in stream:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            continue
        content = getattr(delta, "content", None)
        texts: list[str] = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for item in content:
                text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
                if text:
                    texts.append(str(text))
        if texts:
            parts.extend(texts)
            if not first_output_reported and progress is not None:
                first_output_reported = True
                progress(f"AI first output received at {time.monotonic() - started:.1f}s")
    return "".join(parts).strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if not raw:
        raise OpenAICompatibleResponseError("OpenAI-compatible API 返回空文本。")
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
    raise OpenAICompatibleResponseError(
        "OpenAI-compatible API 未返回可解析的 JSON object。"
        + (f" response_prefix={preview!r}" if preview else "")
    )


def _close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class OpenAICompatibleSemanticProvider:
    """Generic multimodal JSON-task adapter with a real wall-clock deadline."""

    name = "openai-compatible-chat-semantic"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        client: Any | None = None,
        image_detail: str = "auto",
        max_output_tokens: int = 12000,
        structured_mode: str = "prompt_only",
        compat_profile: str = "generic",
        request_timeout_seconds: float = 120.0,
        enable_thinking: bool | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model 不能为空。")
        if not api_key.strip():
            raise ValueError("api_key 不能为空。")
        if not base_url.strip():
            raise ValueError("base_url 不能为空。")
        if image_detail not in {"auto", "low", "high"}:
            raise ValueError("image_detail 必须是 auto/low/high。")
        if max_output_tokens < 1000:
            raise ValueError("max_output_tokens 不能小于 1000。")
        if structured_mode not in {"prompt_only", "json_object"}:
            raise ValueError("structured_mode 必须是 prompt_only/json_object。")
        if compat_profile not in SUPPORTED_COMPAT_PROFILES:
            raise ValueError("compat_profile 必须是 " + "/".join(SUPPORTED_COMPAT_PROFILES) + "。")
        if not 10.0 <= float(request_timeout_seconds) <= 600.0:
            raise ValueError("request_timeout_seconds 必须在 10..600 秒。")
        if enable_thinking not in {None, True, False}:
            raise ValueError("enable_thinking 必须是 bool 或 None。")

        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise OpenAICompatibleProviderError("缺少 openai Python SDK。") from exc
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=float(request_timeout_seconds),
                max_retries=0,
            )

        self.client = client
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self.image_detail = image_detail
        self.max_output_tokens = int(max_output_tokens)
        self.structured_mode = structured_mode
        self.compat_profile = compat_profile
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.enable_thinking = enable_thinking
        self.progress_callback: Callable[[str], None] | None = None

    def set_progress_callback(self, callback: Callable[[str], None] | None) -> None:
        self.progress_callback = callback

    def _progress(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    def _network_text(self, kwargs: dict[str, Any], *, streaming: bool) -> str:
        started = time.monotonic()
        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
        holder: dict[str, Any] = {}

        def worker() -> None:
            try:
                response = self.client.chat.completions.create(**kwargs)
                holder["response"] = response
                self._progress(f"AI connection established at {time.monotonic() - started:.1f}s")
                text = (
                    _extract_stream_text(response, started=started, progress=self._progress)
                    if streaming
                    else _extract_message_text(response)
                )
                result_queue.put_nowait(("ok", text))
            except BaseException as exc:  # daemon worker reports exact failure to caller
                try:
                    result_queue.put_nowait(("error", exc))
                except queue.Full:
                    pass

        thread = threading.Thread(target=worker, name="ai-json-request", daemon=True)
        thread.start()
        deadline = started + self.request_timeout_seconds
        next_progress = started + _PROGRESS_INTERVAL_SECONDS
        self._progress(
            f"AI request started; wall-clock deadline={self.request_timeout_seconds:.0f}s"
        )

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                _close_response(holder.get("response"))
                raise OpenAICompatibleTransportError(
                    f"AI whole-request wall-clock deadline exceeded: "
                    f"{self.request_timeout_seconds:.0f}s"
                )
            wait = min(remaining, max(0.05, next_progress - now))
            try:
                kind, value = result_queue.get(timeout=wait)
            except queue.Empty:
                now = time.monotonic()
                if now >= next_progress:
                    self._progress(
                        f"AI still running: elapsed={now - started:.1f}s / "
                        f"deadline={self.request_timeout_seconds:.0f}s"
                    )
                    next_progress = now + _PROGRESS_INTERVAL_SECONDS
                continue

            _close_response(holder.get("response"))
            if kind == "ok":
                self._progress(f"AI response complete at {time.monotonic() - started:.1f}s")
                return str(value)
            if isinstance(value, OpenAICompatibleProviderError):
                raise value
            raise OpenAICompatibleTransportError(
                f"OpenAI-compatible JSON task 调用失败：{value}"
            ) from value

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        if not request_payload.get("json_contract"):
            raise OpenAICompatibleProviderError("JSON task 缺少 json_contract。")

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": str(
                        request_payload.get("system_instruction")
                        or "You execute the supplied JSON task."
                    ),
                },
                {
                    "role": "user",
                    "content": _message_content(request_payload, image_detail=self.image_detail),
                },
            ],
            "timeout": self.request_timeout_seconds,
        }
        if self.structured_mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
            # Alibaba Cloud explicitly recommends not setting max_tokens in JSON
            # mode because truncation produces invalid JSON.
        else:
            kwargs["max_tokens"] = self.max_output_tokens
        if self.enable_thinking is not None:
            kwargs["extra_body"] = {"enable_thinking": self.enable_thinking}

        streaming = self.compat_profile == "qwen-omni"
        if streaming:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
            kwargs["modalities"] = ["text"]

        output_text = self._network_text(kwargs, streaming=streaming)
        payload = _parse_json_object(output_text)
        payload["extractor"] = self.name
        return payload
