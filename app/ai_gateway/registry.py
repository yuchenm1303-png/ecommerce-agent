from dataclasses import dataclass
from typing import FrozenSet

from .models import ModelCapability


@dataclass(frozen=True)
class ModelSpec:
    name: str
    provider: str
    capabilities: FrozenSet[ModelCapability]
    cost_tier: str = "unknown"


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, ModelSpec] = {}

    def register(self, spec: ModelSpec) -> None:
        self._models[spec.name] = spec

    def get(self, name: str) -> ModelSpec | None:
        return self._models.get(name)

    def find_capable(self, capabilities: set[ModelCapability]) -> list[ModelSpec]:
        return [
            item for item in self._models.values()
            if capabilities.issubset(item.capabilities)
        ]
