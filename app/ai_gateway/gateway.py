from __future__ import annotations

from .models import AIRequest, AIResponse
from .provider import AIProvider


class AIGateway:
    """Stable entry point used by future Agents.

    This first version intentionally has no routing magic. It creates the
    boundary first; routing, fallback and cost policies can be added without
    changing callers.
    """

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    def complete(self, request: AIRequest) -> AIResponse:
        return self._provider.complete(request)
