from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from .errors import JSONTaskProviderError, JSONTaskResponseError, JSONTaskTransportError


class OpenAIProviderError(JSONTaskProviderError):
    pass


class OpenAITransportError(JSONTaskTransportError, OpenAIProviderError):
    pass


class OpenAIResponseError(JSONTaskResponseError, OpenAIProviderError):
    pass


def _image_data_uri(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_file():
        raise OpenAIProviderError(f"OpenAI provider 找不到图片：{path}")
    mime, _ = mimetypes.guess_type(path.name)
    if not mime or not mime.startswith("image/"):
        raise OpenAIProviderError(f"无法识别图片 MIME type：{path}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _prompt_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "task": request_payload.get("task"),
        "product_identity": request_payload.get("product_identity") or {},
        "schema_sha256": request_payload.get("schema_sha256", ""),
        "source_manifest_sha256": request_payload.get("source_manifest_sha256", ""),
        "target_fields": request_payload.get("target_fields") or [],
        "rules": request_payload.get("rules") or [],
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


def _task_instruction(request_payload: dict[str, Any]) -> str:
    parts = [str(request_payload.get("prompt_instruction") or "").strip()]
    validation_error = str(request_payload.get("validation_error") or "").strip()
    if validation_error:
        parts.append(
            "CORRECTION REQUIRED: the prior JSON failed the structural contract: "
            + validation_error
        )
    parts.append("Return one JSON object satisfying the supplied strict schema.")
    return "\n\n".join(part for part in parts if part)


def _input_content(request_payload: dict[str, Any], *, image_detail: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
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
        content.extend(
            [
                {
                    "type": "input_text",
                    "text": (
                        f"The immediately following image is grounded source_id={source_id}. "
                        "Any citation to this image must use exactly that source_reference."
                    ),
                },
                {
                    "type": "input_image",
                    "image_url": _image_data_uri(str(source.get("image_path") or "")),
                    "detail": image_detail,
                },
            ]
        )
    return content


class OpenAISemanticProvider:
    """OpenAI Responses API adapter for grounded multimodal JSON tasks."""

    name = "openai-responses-semantic"

    def __init__(
        self,
        *,
        model: str = "gpt-5.6",
        client: Any | None = None,
        image_detail: str = "high",
        max_output_tokens: int = 12000,
        request_timeout_seconds: float = 120.0,
    ) -> None:
        if not model.strip():
            raise ValueError("OpenAI model 不能为空。")
        if image_detail not in {"auto", "low", "high"}:
            raise ValueError("image_detail 必须是 auto/low/high。")
        if max_output_tokens < 1000:
            raise ValueError("max_output_tokens 不能小于 1000。")
        if not 10.0 <= float(request_timeout_seconds) <= 600.0:
            raise ValueError("request_timeout_seconds 必须在 10..600 秒。")

        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise OpenAIProviderError("缺少 openai Python SDK。") from exc
            client = OpenAI(timeout=float(request_timeout_seconds), max_retries=0)

        self.client = client
        self.model = model.strip()
        self.image_detail = image_detail
        self.max_output_tokens = int(max_output_tokens)
        self.request_timeout_seconds = float(request_timeout_seconds)

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        schema = request_payload.get("json_contract")
        if not isinstance(schema, dict) or not schema:
            raise OpenAIProviderError("JSON task 缺少 json_contract。")
        try:
            response = self.client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": str(
                            request_payload.get("system_instruction")
                            or "You execute the supplied grounded JSON task."
                        ),
                    },
                    {
                        "role": "user",
                        "content": _input_content(
                            request_payload,
                            image_detail=self.image_detail,
                        ),
                    },
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "makro_ai_field_decisions",
                        "strict": True,
                        "schema": schema,
                    }
                },
                max_output_tokens=self.max_output_tokens,
                timeout=self.request_timeout_seconds,
            )
        except Exception as exc:
            raise OpenAITransportError(f"OpenAI JSON task 调用失败：{exc}") from exc

        status = str(getattr(response, "status", "") or "")
        if status and status != "completed":
            raise OpenAIResponseError(
                f"OpenAI JSON task 未完整完成：status={status}, "
                f"details={getattr(response, 'incomplete_details', None)}"
            )
        output_text = str(getattr(response, "output_text", "") or "").strip()
        if not output_text:
            raise OpenAIResponseError("OpenAI JSON task 返回空 output_text。")
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise OpenAIResponseError("OpenAI structured output 不是有效 JSON。") from exc
        if not isinstance(payload, dict):
            raise OpenAIResponseError("OpenAI structured output 顶层必须是 JSON object。")
        payload["extractor"] = self.name
        return payload
