from __future__ import annotations

from enum import Enum


class ModelCapability(str, Enum):
    """Provider-neutral model abilities used for deterministic routing.

    A capability is a contract, not a marketing label. Callers request the
    abilities they require; provider-specific adapters decide how those abilities
    are implemented. Unsupported abilities fail before a model request is sent.
    """

    TEXT = "text"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"
    TOOL_CALLING = "tool_calling"
    WEB_SEARCH = "web_search"
    EMBEDDING = "embedding"
    STREAMING = "streaming"
    REASONING = "reasoning"


__all__ = ["ModelCapability"]
