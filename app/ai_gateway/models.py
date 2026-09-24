from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelCapability(str, Enum):
    TEXT = "text"
    VISION = "vision"
    TOOL_CALLING = "tool_calling"
    JSON = "json"
    REASONING = "reasoning"


@dataclass(frozen=True)
class AIRequest:
    task: str
    messages: list[dict[str, Any]]
    required_capabilities: frozenset[ModelCapability] = field(default_factory=frozenset)
    model_hint: str | None = None


@dataclass(frozen=True)
class AIResponse:
    content: str
    model: str
    provider: str
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    model: str
    capabilities: frozenset[ModelCapability]
    input_cost: float | None = None
    output_cost: float | None = None
