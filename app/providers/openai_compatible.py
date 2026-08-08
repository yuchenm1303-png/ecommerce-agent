from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any


class OpenAICompatibleProviderError(RuntimeError):
    pass


SUPPORTED_COMPAT_PROFILES = ("generic", "qwen-omni")


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
    """Serialize the AI task without leaking local image paths."""

    payload = {
        "task": request_payload.get("task"),
        "product_identity": request_payload.get("product_identity") or {},
        "schema_sha256": request_payload.get("schema_sha256", ""),
        "source_manifest_sha256": request_payload.get("source_manifest_sha256", ""),
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
            "sha256": source.get("sha256", ""),
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
        "Return exactly one JSON object and no markdown. Follow json_contract. "
        "Never invent unsupported product facts."
    )
    return "\n\n".join(part for part in parts if part)


def _message_content(request_payload: dict[str, Any], *, image_detail: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                _task_instruction(request_payload)
                + "\n\n"
                + json.dumps(_prompt_payload(request_payload), ensure_ascii=False)
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
                    f"The immediately following image is grounded source_id={source_id}. "
                    "Any citation to this image must use exactly that source_reference."
                ),
            }
        )
        image_url: dict[str, Any] = {
            "url": _image_data_uri(str(source.get("image_path") or "")),
        }
        if image_detail in {"low", "high"}:
            image_url["detail"] = image_detail
        content.append({"type": "image_url", "image_url": image_url})
    return content


def _extract_message_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise OpenAICompatibleProviderError("OpenAI-compatible API 没有返回 choices。")
    message = getattr(choices[0], "message", None)
    if message is None:
        raise OpenAICompatibleProviderError("OpenAI-compatible API choice 缺少 message。")
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


def _extract_stream_text(stream: Any) -> str:
    parts: list[str] = []
    for chunk in stream:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            continue
        content = getattr(delta, "content", None)
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
                if text:
                    parts.append(str(text))
    return "".join(parts).strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if not raw:
        raise OpenAICompatibleProviderError("OpenAI-compatible API 返回空文本。")
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
    raise OpenAICompatibleProviderError("OpenAI-compatible API 未返回可解析的 JSON object。")


class OpenAICompatibleSemanticProvider:
    """Generic multimodal JSON-task adapter for OpenAI-compatible APIs."""

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

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        if not request_payload.get("json_contract"):
            raise OpenAICompatibleProviderError("JSON task 缺少 json_contract。")
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": str(request_payload.get("system_instruction") or "You execute the supplied JSON task."),
                },
                {
                    "role": "user",
                    "content": _message_content(request_payload, image_detail=self.image_detail),
                },
            ],
            "max_tokens": self.max_output_tokens,
            "timeout": self.request_timeout_seconds,
        }
        if self.structured_mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        if self.enable_thinking is not None:
            kwargs["extra_body"] = {"enable_thinking": self.enable_thinking}

        streaming = self.compat_profile == "qwen-omni"
        if streaming:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
            kwargs["modalities"] = ["text"]
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise OpenAICompatibleProviderError(f"OpenAI-compatible JSON task 调用失败：{exc}") from exc

        output_text = _extract_stream_text(response) if streaming else _extract_message_text(response)
        payload = _parse_json_object(output_text)
        payload["extractor"] = self.name
        return payload
