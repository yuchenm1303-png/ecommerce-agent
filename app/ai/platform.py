from __future__ import annotations

from typing import Any, Protocol

from .capabilities import ModelCapability
from .profiles import ModelProfile, ModelRegistry


class StructuredModelBackend(Protocol):
    """Narrow execution contract already satisfied by current Listing providers."""

    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


class AIPlatform:
    """Provider-neutral model profile router.

    Phase A intentionally exposes only the structured execution lane used by the
    production Listing AI. Future agent/tool/streaming lanes can be added beside
    it without weakening the existing strict JSON contract.
    """

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()
        self._backends: dict[str, StructuredModelBackend] = {}

    def register(self, profile: ModelProfile, backend: StructuredModelBackend) -> None:
        self.registry.register(profile)
        self._backends[profile.profile_id] = backend

    def execute_structured(
        self,
        profile_id: str,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        profile = self.registry.require(
            profile_id,
            capabilities=(ModelCapability.STRUCTURED_OUTPUT,),
        )
        try:
            backend = self._backends[profile.profile_id]
        except KeyError as exc:
            raise RuntimeError(f"model profile is not bound to a backend: {profile.profile_id}") from exc
        # Preserve the exact Listing task packet. Prompt/schema/evidence semantics
        # remain owned by the caller and the existing provider implementation.
        result = backend.extract_json(request_payload)
        if not isinstance(result, dict):
            raise TypeError("structured model backend must return a JSON object")
        return result


__all__ = ["AIPlatform", "StructuredModelBackend"]
