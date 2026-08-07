from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any


class OpenAICompatibleProviderError(RuntimeError):
    pass


_JSON_CONTRACT = {
    "type": "object",
    "required": ["product_identity", "facts", "warnings"],
    "properties": {
        "product_identity": {
            "type": "object",
            "required": ["sku", "model_number", "brand"],
            "properties": {
                "sku": {"type": "string"},
                "model_number": {"type": "string"},
                "brand": {"type": "string"},
            },
        },
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "key",
                    "aliases",
                    "value",
                    "source_type",
                    "source_reference",
                    "confidence",
                    "evidence_text",
                    "note",
                ],
                "properties": {
                    "key": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "value": {"type": "array", "items": {"type": "string"}},
                    "source_type": {"type": "string"},
                    "source_reference": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence_text": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


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
    """Build the vendor-neutral grounded prompt without leaking local file paths."""

    payload = {
        "task": request_payload.get("task"),
        "batch_id": request_payload.get("batch_id", ""),
        "product_identity": request_payload.get("product_identity") or {},
        "questions": request_payload.get("questions") or [],
        "business_locked_questions": request_payload.get("business_locked_questions") or [],
        "rules": request_payload.get("rules") or [],
        "source_reference_rule": request_payload.get("source_reference_rule", ""),
        "required_output_shape": request_payload.get("required_output_shape") or {},
        "grounded_sources": [],
        "json_contract": _JSON_CONTRACT,
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


def _message_content(request_payload: dict[str, Any], *, image_detail: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Return exactly one JSON object and no markdown. Follow the supplied json_contract. "
                "Never invent a fact when evidence is absent or ambiguous.\n\n"
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
                    "Any fact citing this image must use exactly that source_reference."
                ),
            }
        )
        image_url: dict[str, Any] = {
            "url": _image_data_uri(str(source.get("image_path") or "")),
        }
        # Many OpenAI-compatible APIs accept detail; some ignore it. Keeping it
        # inside image_url follows the common Chat Completions multimodal shape.
        if image_detail:
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
            if isinstance(item, dict):
                text = item.get("text")
            else:
                text = getattr(item, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if not raw:
        raise OpenAICompatibleProviderError("OpenAI-compatible API 返回空文本。")

    candidates = [raw]
    if raw.startswith("```") and raw.endswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3:
            candidates.append("\n".join(lines[1:-1]).strip())
    first = raw.find("{")
    last = raw.rfind("}")
    if 0 <= first < last:
        candidates.append(raw[first : last + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise OpenAICompatibleProviderError(
        "OpenAI-compatible API 未返回可解析的 JSON object；可尝试 --structured-mode json_object，"
        "或确认该模型支持按指令输出 JSON。"
    )


class OpenAICompatibleSemanticProvider:
    """Generic multimodal adapter for OpenAI-compatible Chat Completions APIs.

    This adapter intentionally does not trust the model response. It only turns
    a provider response into an untrusted JSON candidate. The existing grounded
    semantic validation layer still enforces exact QA keys, source references,
    literal evidence, identity guards, business locks, conflict handling and
    confidence ceilings before anything can reach the resolver.
    """

    name = "openai-compatible-chat-semantic"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        client: Any | None = None,
        image_detail: str = "high",
        max_output_tokens: int = 12000,
        structured_mode: str = "prompt_only",
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

        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise OpenAICompatibleProviderError(
                    "缺少 openai Python SDK。请先安装 requirements.txt。"
                ) from exc
            client = OpenAI(api_key=api_key, base_url=base_url)

        self.client = client
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self.image_detail = image_detail
        self.max_output_tokens = int(max_output_tokens)
        self.structured_mode = structured_mode

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You extract auditable product facts only from supplied grounded sources. "
                        "Do not use unstated knowledge. Return no fact rather than guessing."
                    ),
                },
                {
                    "role": "user",
                    "content": _message_content(
                        request_payload,
                        image_detail=self.image_detail,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
        }
        if self.structured_mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible semantic extraction 调用失败：{exc}"
            ) from exc

        payload = _parse_json_object(_extract_message_text(response))
        payload["extractor"] = self.name
        return payload
