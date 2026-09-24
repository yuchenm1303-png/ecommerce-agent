from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from .capabilities import ModelCapability
from .contracts import ChatRequest, ModelResponse, StreamEvent, StructuredRequest
from .profiles import ModelProfile, ModelRegistry


class StructuredModelBackend(Protocol):
    """Legacy narrow structured contract kept for detached compatibility tests."""

    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


class ChatModelBackend(Protocol):
    name: str

    def complete(self, request: ChatRequest) -> ModelResponse:
        ...

    def complete_structured(self, request: StructuredRequest) -> dict[str, Any]:
        ...

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        ...


class AIPlatform:
    """Provider-neutral runtime router keyed only by stable model profile IDs."""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()
        self._backends: dict[str, object] = {}

    def register(self, profile: ModelProfile, backend: object) -> None:
        self.registry.register(profile)
        self._backends[profile.profile_id] = backend

    def _backend_for(self, profile_id: str) -> tuple[ModelProfile, object]:
        profile = self.registry.get(profile_id)
        try:
            backend = self._backends[profile.profile_id]
        except KeyError as exc:
            raise RuntimeError(f"model profile is not bound to a backend: {profile.profile_id}") from exc
        return profile, backend

    def _require_chat_capabilities(
        self,
        profile_id: str,
        request: ChatRequest,
        *,
        streaming: bool = False,
        structured: bool = False,
    ) -> ModelProfile:
        required = {ModelCapability.TEXT}
        if request.uses_vision:
            required.add(ModelCapability.VISION)
        if request.tools:
            required.add(ModelCapability.TOOL_CALLING)
        if streaming:
            required.add(ModelCapability.STREAMING)
        if structured:
            required.add(ModelCapability.STRUCTURED_OUTPUT)
        return self.registry.require(profile_id, capabilities=required)

    def execute_chat(self, profile_id: str, request: ChatRequest) -> ModelResponse:
        self._require_chat_capabilities(profile_id, request)
        _profile, backend = self._backend_for(profile_id)
        complete = getattr(backend, "complete", None)
        if not callable(complete):
            raise RuntimeError(f"backend for {profile_id!r} does not support chat completion")
        result = complete(request)
        if not isinstance(result, ModelResponse):
            raise TypeError("chat model backend must return ModelResponse")
        return result

    def execute_structured_chat(
        self,
        profile_id: str,
        request: StructuredRequest,
    ) -> dict[str, Any]:
        self._require_chat_capabilities(profile_id, request.chat, structured=True)
        _profile, backend = self._backend_for(profile_id)
        complete_structured = getattr(backend, "complete_structured", None)
        if not callable(complete_structured):
            raise RuntimeError(f"backend for {profile_id!r} does not support structured output")
        result = complete_structured(request)
        if not isinstance(result, dict):
            raise TypeError("structured model backend must return a JSON object")
        return result

    def stream_chat(self, profile_id: str, request: ChatRequest) -> Iterator[StreamEvent]:
        self._require_chat_capabilities(profile_id, request, streaming=True)
        _profile, backend = self._backend_for(profile_id)
        stream = getattr(backend, "stream", None)
        if not callable(stream):
            raise RuntimeError(f"backend for {profile_id!r} does not support streaming")
        for event in stream(request):
            if not isinstance(event, StreamEvent):
                raise TypeError("streaming model backend must yield StreamEvent values")
            yield event

    def execute_structured(
        self,
        profile_id: str,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Legacy structured packet lane retained until Listing migration is explicit."""

        self.registry.require(
            profile_id,
            capabilities=(ModelCapability.STRUCTURED_OUTPUT,),
        )
        _profile, backend = self._backend_for(profile_id)
        extract_json = getattr(backend, "extract_json", None)
        if not callable(extract_json):
            raise RuntimeError(f"backend for {profile_id!r} does not support legacy JSON tasks")
        result = extract_json(request_payload)
        if not isinstance(result, dict):
            raise TypeError("structured model backend must return a JSON object")
        return result


__all__ = ["AIPlatform", "ChatModelBackend", "StructuredModelBackend"]
