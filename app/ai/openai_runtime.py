from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .contracts import (
    AIMessage,
    ChatRequest,
    ImagePart,
    ModelResponse,
    ModelUsage,
    StreamEvent,
    StreamEventKind,
    StructuredOutputMode,
    StructuredRequest,
    TextPart,
    ToolCall,
)
from .errors import AIResponseError, AITransportError
from .profiles import ModelProfile
from .provider_catalog import ProviderAdapter, ProviderConnection


def _message_payload(message: AIMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role.value}
    if isinstance(message.content, str):
        payload["content"] = message.content
    else:
        content: list[dict[str, Any]] = []
        for part in message.content:
            if isinstance(part, TextPart):
                content.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": part.image_url, "detail": part.detail},
                    }
                )
            else:  # pragma: no cover - contracts reject unsupported parts
                raise TypeError("unsupported message content part")
        payload["content"] = content
    if message.name:
        payload["name"] = message.name
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(
                        call.arguments,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            }
            for call in message.tool_calls
        ]
    return payload


def _tool_payload(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _usage_from(response: Any) -> ModelUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return ModelUsage()
    input_tokens = int(
        getattr(usage, "prompt_tokens", None)
        or getattr(usage, "input_tokens", None)
        or 0
    )
    output_tokens = int(
        getattr(usage, "completion_tokens", None)
        or getattr(usage, "output_tokens", None)
        or 0
    )
    total_tokens = int(getattr(usage, "total_tokens", None) or input_tokens + output_tokens)
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _parse_tool_calls(message: Any) -> tuple[ToolCall, ...]:
    parsed: list[ToolCall] = []
    for raw_call in getattr(message, "tool_calls", None) or ():
        function = getattr(raw_call, "function", None)
        call_id = str(getattr(raw_call, "id", "") or "").strip()
        name = str(getattr(function, "name", "") or "").strip()
        raw_arguments = str(getattr(function, "arguments", "") or "").strip()
        if not call_id or not name:
            raise AIResponseError("tool call is missing id or function name")
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError as exc:
            raise AIResponseError(f"tool call {name!r} returned invalid JSON arguments") from exc
        if not isinstance(arguments, dict):
            raise AIResponseError(f"tool call {name!r} arguments must be a JSON object")
        parsed.append(ToolCall(call_id=call_id, name=name, arguments=arguments))
    return tuple(parsed)


class OpenAIChatBackend:
    """Unified Chat Completions runtime for OpenAI and OpenAI-compatible endpoints."""

    def __init__(
        self,
        *,
        connection: ProviderConnection,
        profile: ModelProfile,
        api_key: str,
        client: Any | None = None,
        request_timeout_seconds: float = 120.0,
    ) -> None:
        if connection.provider_id != profile.provider:
            raise ValueError("provider connection/profile mismatch")
        if connection.adapter not in {
            ProviderAdapter.OPENAI,
            ProviderAdapter.OPENAI_COMPATIBLE,
        }:
            raise ValueError(f"unsupported OpenAI runtime adapter: {connection.adapter.value}")
        api_key = str(api_key or "").strip()
        if not api_key:
            raise ValueError("api_key must not be empty")
        timeout = float(request_timeout_seconds)
        if not 10.0 <= timeout <= 600.0:
            raise ValueError("request_timeout_seconds must be within 10..600")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("openai Python SDK is required") from exc
            kwargs: dict[str, Any] = {
                "api_key": api_key,
                "timeout": timeout,
                "max_retries": 0,
            }
            if connection.adapter is ProviderAdapter.OPENAI_COMPATIBLE:
                kwargs["base_url"] = connection.base_url
            client = OpenAI(**kwargs)
        self.connection = connection
        self.profile = profile
        self.client = client
        self.request_timeout_seconds = timeout

    @property
    def name(self) -> str:
        return f"{self.connection.adapter.value}-chat"

    def _request_kwargs(self, request: ChatRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.profile.model,
            "messages": [_message_payload(message) for message in request.messages],
            "timeout": self.request_timeout_seconds,
        }
        if request.tools:
            kwargs["tools"] = [_tool_payload(tool) for tool in request.tools]
            kwargs["tool_choice"] = request.tool_choice.value
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_tokens"] = request.max_output_tokens
        return kwargs

    def _create(self, kwargs: dict[str, Any]) -> Any:
        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise AITransportError(
                f"AI request failed via provider {self.connection.provider_id!r}: {type(exc).__name__}: {exc}"
            ) from exc

    def complete(self, request: ChatRequest) -> ModelResponse:
        if not isinstance(request, ChatRequest):
            raise TypeError("request must be ChatRequest")
        response = self._create(self._request_kwargs(request))
        choices = getattr(response, "choices", None) or ()
        if not choices:
            raise AIResponseError("AI response contained no choices")
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            raise AIResponseError("AI response choice contained no message")
        text = str(getattr(message, "content", "") or "")
        tool_calls = _parse_tool_calls(message)
        if not text and not tool_calls:
            raise AIResponseError("AI response contained neither text nor tool calls")
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            usage=_usage_from(response),
            finish_reason=str(getattr(choice, "finish_reason", "") or ""),
            response_id=str(getattr(response, "id", "") or ""),
        )

    def _effective_structured_mode(self, requested: StructuredOutputMode) -> StructuredOutputMode:
        if requested is not StructuredOutputMode.AUTO:
            return requested
        if self.connection.adapter is ProviderAdapter.OPENAI:
            return StructuredOutputMode.JSON_SCHEMA
        return StructuredOutputMode.JSON_OBJECT

    def complete_structured(self, request: StructuredRequest) -> dict[str, Any]:
        if not isinstance(request, StructuredRequest):
            raise TypeError("request must be StructuredRequest")
        kwargs = self._request_kwargs(request.chat)
        mode = self._effective_structured_mode(request.mode)
        if mode is StructuredOutputMode.JSON_SCHEMA:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": True,
                    "schema": request.json_schema,
                },
            }
        else:
            schema_instruction = (
                "Return exactly one JSON object matching this JSON Schema. Do not emit markdown.\n"
                + json.dumps(request.json_schema, ensure_ascii=False, separators=(",", ":"))
            )
            kwargs["messages"] = [
                {"role": "system", "content": schema_instruction},
                *kwargs["messages"],
            ]
            if mode is StructuredOutputMode.JSON_OBJECT:
                kwargs["response_format"] = {"type": "json_object"}
            elif mode is not StructuredOutputMode.PROMPT_ONLY:
                raise ValueError(f"unsupported structured output mode: {mode.value}")

        response = self._create(kwargs)
        choices = getattr(response, "choices", None) or ()
        if not choices:
            raise AIResponseError("structured AI response contained no choices")
        message = getattr(choices[0], "message", None)
        text = str(getattr(message, "content", "") or "").strip() if message is not None else ""
        if not text:
            raise AIResponseError("structured AI response contained empty content")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIResponseError("structured AI response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise AIResponseError("structured AI response top level must be a JSON object")
        return payload

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        if not isinstance(request, ChatRequest):
            raise TypeError("request must be ChatRequest")
        kwargs = self._request_kwargs(request)
        kwargs["stream"] = True
        stream = self._create(kwargs)
        try:
            for chunk in stream:
                choices = getattr(chunk, "choices", None) or ()
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    text = str(getattr(delta, "content", "") or "")
                    if text:
                        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta=text)
                    for raw_call in getattr(delta, "tool_calls", None) or ():
                        function = getattr(raw_call, "function", None)
                        yield StreamEvent(
                            kind=StreamEventKind.TOOL_CALL_DELTA,
                            tool_call_index=(
                                int(getattr(raw_call, "index"))
                                if getattr(raw_call, "index", None) is not None
                                else None
                            ),
                            tool_call_id=str(getattr(raw_call, "id", "") or ""),
                            tool_name=str(getattr(function, "name", "") or ""),
                            arguments_delta=str(getattr(function, "arguments", "") or ""),
                        )
                finish_reason = str(getattr(choice, "finish_reason", "") or "")
                if finish_reason:
                    yield StreamEvent(
                        kind=StreamEventKind.COMPLETED,
                        finish_reason=finish_reason,
                    )
        except AITransportError:
            raise
        except Exception as exc:
            raise AITransportError(
                f"AI stream failed via provider {self.connection.provider_id!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc


__all__ = ["OpenAIChatBackend"]
