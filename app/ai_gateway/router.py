from .models import AIRequest
from .registry import ModelRegistry, ModelSpec


class ModelRouter:
    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry

    def select(self, request: AIRequest) -> ModelSpec | None:
        candidates = self.registry.find_capable(request.required_capabilities)
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item.cost_tier)[0]
