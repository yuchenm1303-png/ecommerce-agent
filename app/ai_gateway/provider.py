from __future__ import annotations

from abc import ABC, abstractmethod

from .models import AIRequest, AIResponse, ModelProfile


class AIProvider(ABC):
    """Common boundary for every model vendor.

    Providers own SDK/API details. Business code must not import Qwen/OpenAI
    clients directly.
    """

    @property
    @abstractmethod
    def profile(self) -> ModelProfile:
        raise NotImplementedError

    @abstractmethod
    def complete(self, request: AIRequest) -> AIResponse:
        raise NotImplementedError
