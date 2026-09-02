from __future__ import annotations

from typing import Any

import pytest

from app.ai import (
    AIPlatform,
    LISTING_SEMANTIC_PROFILE_ID,
    ModelCapability,
    ModelProfile,
    ModelRegistry,
    listing_semantic_profile,
)
from app.providers.openai_compatible import OpenAICompatibleSemanticProvider
from app.providers.registry import ProviderConfig, build_semantic_provider


class _StructuredBackend:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(request_payload)
        return {"ok": True}


class _DummyCreate:
    def __call__(self, **_kwargs: Any) -> None:
        return None


class _DummyClient:
    def __init__(self) -> None:
        from types import SimpleNamespace

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_DummyCreate()))


def test_model_profile_is_provider_neutral_and_contains_no_credentials() -> None:
    profile = listing_semantic_profile(provider="openai-compatible", model="qwen-model")

    assert profile.profile_id == LISTING_SEMANTIC_PROFILE_ID
    assert profile.allow_fallback is False
    assert profile.supports(
        ModelCapability.TEXT,
        ModelCapability.STRUCTURED_OUTPUT,
        ModelCapability.VISION,
    )
    assert ModelCapability.TOOL_CALLING not in profile.capabilities
    assert ModelCapability.WEB_SEARCH not in profile.capabilities
    assert not any(
        token in {field.casefold() for field in profile.__dataclass_fields__}
        for token in ("api_key", "token", "password", "secret")
    )


def test_model_registry_rejects_duplicate_roles_and_missing_capabilities() -> None:
    registry = ModelRegistry()
    profile = ModelProfile(
        profile_id="agent.fast",
        provider="vendor",
        model="fast-model",
        capabilities=frozenset({ModelCapability.TEXT}),
    )
    registry.register(profile)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(profile)
    with pytest.raises(ValueError, match="lacks required capabilities"):
        registry.require("agent.fast", (ModelCapability.STRUCTURED_OUTPUT,))
    with pytest.raises(KeyError, match="unknown model profile"):
        registry.get("missing.profile")


def test_ai_platform_delegates_exact_structured_task_without_mutation() -> None:
    backend = _StructuredBackend()
    platform = AIPlatform()
    profile = ModelProfile(
        profile_id="listing.test",
        provider="vendor",
        model="model-a",
        capabilities=frozenset({ModelCapability.TEXT, ModelCapability.STRUCTURED_OUTPUT}),
    )
    platform.register(profile, backend)
    request = {"task": "identity", "json_contract": {"type": "object"}}

    result = platform.execute_structured("listing.test", request)

    assert result == {"ok": True}
    assert backend.calls == [request]
    assert backend.calls[0] is request


def test_listing_provider_registry_is_now_bound_to_pinned_ai_profile() -> None:
    provider = build_semantic_provider(
        ProviderConfig(
            provider="openai-compatible",
            model="vision-model",
            api_key_env="VENDOR_KEY",
            base_url="https://api.vendor.test/v1",
        ),
        environ={"VENDOR_KEY": "secret"},
        client=_DummyClient(),
    )

    assert provider.ai_profile_id == LISTING_SEMANTIC_PROFILE_ID
    assert provider.model_profile.allow_fallback is False
    assert provider.model_profile.provider == "openai-compatible"
    assert provider.model_profile.model == "vision-model"
    # Preserve the existing compatibility surface used by current diagnostics/tests.
    assert isinstance(provider._delegate, OpenAICompatibleSemanticProvider)
    assert provider.model == "vision-model"
