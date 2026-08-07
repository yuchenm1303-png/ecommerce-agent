from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any


class OpenAIProviderError(RuntimeError):
    pass


_EVIDENCE_PACKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "extractor": {"type": "string"},
        "product_identity": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "sku": {"type": "string"},
                "model_number": {"type": "string"},
                "brand": {"type": "string"},
            },
            "required": ["sku", "model_number", "brand"],
        },
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "value": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "source_type": {
                        "type": "string",
                        "enum": [
                            "manufacturer_doc",
                            "supplier_doc",
                            "product_image",
                            "official_doc",
                            "official_web",
                            "supplier_web",
                            "knowledge_base",
                            "customer_file",
                            "ai_synthesis",
                        ],
                    },
                    "source_reference": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence_text": {"type": "string"},
                    "note": {"type": "string"},
                },
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
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["extractor", "product_identity", "facts", "warnings"],
}


def _image_data_uri(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_file():
        raise OpenAIProviderError(f"OpenAI semantic provider 找不到图片：{path}")
    mime, _ = mimetypes.guess_type(path.name)
    if not mime or not mime.startswith("image/"):
        raise OpenAIProviderError(f"无法识别图片 MIME type：{path}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _prompt_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    """Remove local paths from the textual prompt while preserving source ids."""

    payload = {
        "task": request_payload.get("task"),
        "batch_id": request_payload.get("batch_id", ""),
        "product_identity": request_payload.get("product_identity") or {},
        "questions": request_payload.get("questions") or [],
        "business_locked_questions": request_payload.get("business_locked_questions") or [],
        "rules": request_payload.get("rules") or [],
        "source_reference_rule": request_payload.get("source_reference_rule", ""),
        "grounded_sources": [],
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


def _input_content(request_payload: dict[str, Any], *, image_detail: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": json.dumps(_prompt_payload(request_payload), ensure_ascii=False),
        }
    ]
    for source in request_payload.get("grounded_sources") or []:
        if not isinstance(source, dict) or source.get("kind") != "image":
            continue
        source_id = str(source.get("source_id") or "")
        content.append(
            {
                "type": "input_text",
                "text": (
                    f"The immediately following image is grounded source_id={source_id}. "
                    "Any fact citing this image must use exactly that source_reference."
                ),
            }
        )
        content.append(
            {
                "type": "input_image",
                "image_url": _image_data_uri(str(source.get("image_path") or "")),
                "detail": image_detail,
            }
        )
    return content


class OpenAISemanticProvider:
    """OpenAI Responses API adapter for the provider-neutral semantic boundary.

    The adapter only obtains structured candidate facts. It does *not* decide
    whether they are trusted. app.semantic_extraction validates QA keys, source
    ids, literal text evidence, source types, identity and business-field rules
    after this method returns.
    """

    name = "openai-responses-semantic"

    def __init__(
        self,
        *,
        model: str = "gpt-5.6",
        client: Any | None = None,
        image_detail: str = "high",
        max_output_tokens: int = 12000,
    ) -> None:
        if not model.strip():
            raise ValueError("OpenAI model 不能为空。")
        if image_detail not in {"auto", "low", "high"}:
            raise ValueError("image_detail 必须是 auto/low/high。")
        if max_output_tokens < 1000:
            raise ValueError("max_output_tokens 不能小于 1000。")

        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise OpenAIProviderError(
                    "缺少 openai Python SDK。请先安装 requirements.txt。"
                ) from exc
            client = OpenAI()

        self.client = client
        self.model = model.strip()
        self.image_detail = image_detail
        self.max_output_tokens = int(max_output_tokens)

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        content = _input_content(request_payload, image_detail=self.image_detail)
        try:
            response = self.client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You extract auditable product facts from only the supplied grounded sources. "
                            "Never use unstated knowledge. Return no fact rather than guessing."
                        ),
                    },
                    {"role": "user", "content": content},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "makro_grounded_evidence_packet",
                        "strict": True,
                        "schema": _EVIDENCE_PACKET_SCHEMA,
                    }
                },
                max_output_tokens=self.max_output_tokens,
            )
        except Exception as exc:  # SDK/API errors are surfaced; no silent retry here.
            raise OpenAIProviderError(f"OpenAI semantic extraction 调用失败：{exc}") from exc

        status = str(getattr(response, "status", "") or "")
        if status and status != "completed":
            details = getattr(response, "incomplete_details", None)
            raise OpenAIProviderError(
                f"OpenAI semantic extraction 未完整完成：status={status}, details={details}"
            )

        output_text = str(getattr(response, "output_text", "") or "").strip()
        if not output_text:
            raise OpenAIProviderError("OpenAI semantic extraction 返回空 output_text。")
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise OpenAIProviderError("OpenAI structured output 不是有效 JSON。") from exc
        if not isinstance(payload, dict):
            raise OpenAIProviderError("OpenAI structured output 顶层必须是 JSON object。")

        # Provider identity is an execution fact, not something the model chooses.
        payload["extractor"] = self.name
        return payload
