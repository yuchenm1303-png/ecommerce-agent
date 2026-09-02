from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolChoice(str, Enum):
    AUTO = "auto"
    NONE = "none"
    REQUIRED = "required"


class StructuredOutputMode(str, Enum):
    AUTO = "auto"
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    PROMPT_ONLY = "prompt_only"


class StreamEventKind(str, Enum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL_DELTA = "tool_call_delta"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str

    def __post_init__(self) -> None:
        text = str(self.text or "")
        if not text:
            raise ValueError("text content part must not be empty")
        object.__setattr__(self, "text", text)


@dataclass(frozen=True, slots=True)
class ImagePart:
    image_url: str
    detail: str = "auto"

    def __post_init__(self) -> None:
        image_url = str(self.image_url or "").strip()
        detail = str(self.detail or "auto").strip().casefold()
        if not image_url:
            raise ValueError("image_url must not be empty")
        if detail not in {"auto", "low", "high"}:
            raise ValueError("image detail must be auto/low/high")
        object.__setattr__(self, "image_url", image_url)
        object.__setattr__(self, "detail", detail)


ContentPart = TextPart | ImagePart
MessageContent = str | tuple[ContentPart, ...]


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        call_id = str(self.call_id or "").strip()
        name = str(self.name or "").strip()
        if not call_id:
            raise ValueError("tool call id must not be empty")
        if not name:
            raise ValueError("tool call name must not be empty")
        if not isinstance(self.arguments, dict):
            raise TypeError("tool call arguments must be a JSON object")
        object.__setattr__(self, "call_id", call_id)
        object.__setattr__(self, "name", name)


@dataclass(frozen=True, slots=True)
class AIMessage:
    role: MessageRole
    content: MessageContent
    name: str = ""
    tool_call_id: str = ""
    tool_calls: tuple[ToolCall, ...] = ()

    def __post_init__(self) -> None:
        role = MessageRole(self.role)
        content = self.content
        tool_calls = tuple(self.tool_calls)
        if any(not isinstance(call, ToolCall) for call in tool_calls):
            raise TypeError("message tool_calls must contain ToolCall values")
        if len({call.call_id for call in tool_calls}) != len(tool_calls):
            raise ValueError("message tool call ids must be unique")
        if isinstance(content, str):
            if not content and role is not MessageRole.ASSISTANT:
                raise ValueError("message content must not be empty")
        else:
            content = tuple(content)
            if not content:
                raise ValueError("multipart message content must not be empty")
            if any(not isinstance(part, (TextPart, ImagePart)) for part in content):
                raise TypeError("unsupported message content part")
        name = str(self.name or "").strip()
        tool_call_id = str(self.tool_call_id or "").strip()
        if role is MessageRole.TOOL and not tool_call_id:
            raise ValueError("tool messages require tool_call_id")
        if tool_calls and role is not MessageRole.ASSISTANT:
            raise ValueError("only assistant messages may contain tool_calls")
        if role is MessageRole.TOOL and tool_calls:
            raise ValueError("tool messages cannot contain tool_calls")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "tool_call_id", tool_call_id)
        object.__setattr__(self, "tool_calls", tool_calls)

    @property
    def uses_vision(self) -> bool:
        return not isinstance(self.content, str) and any(
            isinstance(part, ImagePart) for part in self.content
        )


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        description = str(self.description or "").strip()
        if not name:
            raise ValueError("tool name must not be empty")
        if not isinstance(self.input_schema, dict) or not self.input_schema:
            raise ValueError("tool input_schema must be a non-empty JSON schema object")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)


@dataclass(frozen=True, slots=True)
class ChatRequest:
    messages: tuple[AIMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    tool_choice: ToolChoice = ToolChoice.AUTO
    temperature: float | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        tools = tuple(self.tools)
        if not messages:
            raise ValueError("chat request requires at least one message")
        if any(not isinstance(message, AIMessage) for message in messages):
            raise TypeError("messages must contain AIMessage values")
        if any(not isinstance(tool, ToolDefinition) for tool in tools):
            raise TypeError("tools must contain ToolDefinition values")
        tool_choice = ToolChoice(self.tool_choice)
        if not tools and tool_choice is ToolChoice.REQUIRED:
            raise ValueError("tool_choice=required requires at least one tool")
        temperature = self.temperature
        if temperature is not None and not 0.0 <= float(temperature) <= 2.0:
            raise ValueError("temperature must be within 0..2")
        max_output_tokens = self.max_output_tokens
        if max_output_tokens is not None and int(max_output_tokens) < 1:
            raise ValueError("max_output_tokens must be positive")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "tool_choice", tool_choice)
        if temperature is not None:
            object.__setattr__(self, "temperature", float(temperature))
        if max_output_tokens is not None:
            object.__setattr__(self, "max_output_tokens", int(max_output_tokens))

    @property
    def uses_vision(self) -> bool:
        return any(message.uses_vision for message in self.messages)


@dataclass(frozen=True, slots=True)
class StructuredRequest:
    chat: ChatRequest
    json_schema: dict[str, Any]
    schema_name: str = "structured_output"
    mode: StructuredOutputMode = StructuredOutputMode.AUTO

    def __post_init__(self) -> None:
        if not isinstance(self.chat, ChatRequest):
            raise TypeError("chat must be ChatRequest")
        if not isinstance(self.json_schema, dict) or not self.json_schema:
            raise ValueError("json_schema must be a non-empty object")
        schema_name = str(self.schema_name or "").strip()
        if not schema_name:
            raise ValueError("schema_name must not be empty")
        object.__setattr__(self, "schema_name", schema_name)
        object.__setattr__(self, "mode", StructuredOutputMode(self.mode))


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage = field(default_factory=ModelUsage)
    finish_reason: str = ""
    response_id: str = ""


@dataclass(frozen=True, slots=True)
class StreamEvent:
    kind: StreamEventKind
    text_delta: str = ""
    tool_call_index: int | None = None
    tool_call_id: str = ""
    tool_name: str = ""
    arguments_delta: str = ""
    finish_reason: str = ""


__all__ = [
    "AIMessage",
    "ChatRequest",
    "ContentPart",
    "ImagePart",
    "MessageContent",
    "MessageRole",
    "ModelResponse",
    "ModelUsage",
    "StreamEvent",
    "StreamEventKind",
    "StructuredOutputMode",
    "StructuredRequest",
    "TextPart",
    "ToolCall",
    "ToolChoice",
    "ToolDefinition",
]
