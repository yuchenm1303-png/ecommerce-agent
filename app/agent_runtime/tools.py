from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.ai import ToolDefinition

from .contracts import ToolEffect


_TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
ToolHandler = Callable[["ToolContext", dict[str, Any]], "ToolResult"]
CancelCheck = Callable[[], bool]


def _never_cancelled() -> bool:
    return False


@dataclass(frozen=True, slots=True)
class ToolContext:
    session_id: str
    turn_id: str
    workspace: Path
    is_cancelled: CancelCheck = _never_cancelled

    @property
    def cancelled(self) -> bool:
        return bool(self.is_cancelled())

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError("agent turn cancellation requested")

    def resolve_workspace_path(self, relative_path: str) -> Path:
        value = str(relative_path or "").strip()
        if not value:
            raise ValueError("workspace path must not be empty")
        candidate = (self.workspace / value).resolve()
        root = self.workspace.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("tool path escapes the agent workspace") from exc
        return candidate


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        content = str(self.content or "")
        if not isinstance(self.data, dict):
            raise TypeError("tool result data must be a JSON object")
        try:
            json.dumps(self.data, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise TypeError("tool result data must be JSON serializable") from exc
        object.__setattr__(self, "content", content)

    def model_payload(self, *, max_chars: int) -> str:
        limit = max(1, int(max_chars))
        payload: dict[str, Any] = {
            "ok": bool(self.ok),
            "content": self.content,
            "data": self.data,
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(serialized) <= limit:
            return serialized
        reserve = 180
        truncated = self.content[: max(0, limit - reserve)]
        return json.dumps(
            {
                "ok": bool(self.ok),
                "content": truncated,
                "data": {"truncated": True},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    effect: ToolEffect = ToolEffect.READ_ONLY

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        description = str(self.description or "").strip()
        if not _TOOL_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid agent tool name: {self.name!r}")
        if not description:
            raise ValueError("agent tool description must not be empty")
        if not isinstance(self.input_schema, dict) or self.input_schema.get("type") != "object":
            raise ValueError("agent tool input_schema must be an object JSON schema")
        if not callable(self.handler):
            raise TypeError("agent tool handler must be callable")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "effect", ToolEffect(self.effect))

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    auto_approved_effects: frozenset[ToolEffect] = frozenset({ToolEffect.READ_ONLY})

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "auto_approved_effects",
            frozenset(ToolEffect(value) for value in self.auto_approved_effects),
        )

    def requires_approval(self, tool: AgentTool) -> bool:
        return tool.effect not in self.auto_approved_effects


class ToolRegistry:
    def __init__(self, tools: tuple[AgentTool, ...] = ()) -> None:
        self._tools: dict[str, AgentTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        if not isinstance(tool, AgentTool):
            raise TypeError("tool must be AgentTool")
        if tool.name in self._tools:
            raise ValueError(f"agent tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(str(name or "").strip())

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[name].definition() for name in sorted(self._tools))

    def all(self) -> tuple[AgentTool, ...]:
        return tuple(self._tools[name] for name in sorted(self._tools))


def validate_tool_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    _validate_value(schema, arguments, path="$", root=True)


def _validate_value(schema: dict[str, Any], value: Any, *, path: str, root: bool = False) -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"invalid tool schema at {path}")
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            raise ValueError(f"invalid properties schema at {path}")
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                raise ValueError(f"{path}.{key} is required")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ValueError(f"{path} contains unsupported properties: {', '.join(unknown)}")
        for key, item in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                _validate_value(child_schema, item, path=f"{path}.{key}")
    elif expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_value(item_schema, item, path=f"{path}[{index}]")
    elif expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} must be a string")
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{path} must be an integer")
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path} must be a number")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path} must be a boolean")
    elif expected not in {None, "null"}:
        raise ValueError(f"unsupported JSON schema type at {path}: {expected}")

    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']!r}")
    if root and expected != "object":
        raise ValueError("tool root schema must be type=object")


__all__ = [
    "AgentTool",
    "CancelCheck",
    "ToolContext",
    "ToolHandler",
    "ToolPolicy",
    "ToolRegistry",
    "ToolResult",
    "validate_tool_arguments",
]
