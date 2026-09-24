from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.ai import (
    AGENT_FAST_ROLE,
    AGENT_REASONING_ROLE,
    AIPlatform,
    CredentialRef,
    LISTING_ATTRIBUTES_ROLE,
    LISTING_IDENTITY_ROLE,
    LISTING_SEMANTIC_PROFILE_ID,
    LISTING_VISION_ROLE,
    LISTING_WEB_RESEARCH_ROLE,
    ModelCapability,
    ModelProfile,
    ModelRegistry,
    ProviderAdapter,
    ProviderCatalog,
    ProviderConnection,
    listing_semantic_profile,
    provider_descriptor,
)


class _StructuredBackend:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(request_payload)
        return {"ok": True}


def test_model_profile_is_provider_neutral_and_contains_only_credential_reference() -> None:
    credential_ref = CredentialRef.environment("DASHSCOPE_API_KEY")
    profile = listing_semantic_profile(
        provider="openai-compatible",
        model="qwen-model",
        credential_ref=credential_ref,
    )

    assert profile.profile_id == LISTING_SEMANTIC_PROFILE_ID
    assert profile.allow_fallback is False
    assert profile.supports(
        ModelCapability.TEXT,
        ModelCapability.STRUCTURED_OUTPUT,
        ModelCapability.VISION,
    )
    assert ModelCapability.TOOL_CALLING not in profile.capabilities
    assert ModelCapability.WEB_SEARCH not in profile.capabilities
    assert profile.credential_ref == credential_ref
    assert profile.as_safe_dict()["credential_ref"] == {
        "source": "environment",
        "name": "DASHSCOPE_API_KEY",
    }
    assert not any(
        token in {field.casefold() for field in profile.__dataclass_fields__}
        for token in ("api_key", "token", "password", "secret")
    )


def test_credential_reference_rejects_secret_value_shaped_environment_name() -> None:
    with pytest.raises(ValueError, match="invalid environment credential reference"):
        CredentialRef.environment("sk-live-secret")


def test_listing_roles_are_separate_and_fail_closed_on_missing_capabilities() -> None:
    assert LISTING_IDENTITY_ROLE.role_id == "listing.identity"
    assert LISTING_ATTRIBUTES_ROLE.role_id == "listing.attributes"
    assert LISTING_VISION_ROLE.role_id == "listing.vision"
    assert LISTING_WEB_RESEARCH_ROLE.role_id == "listing.web_research"
    assert all(
        role.allow_fallback is False
        for role in (
            LISTING_IDENTITY_ROLE,
            LISTING_ATTRIBUTES_ROLE,
            LISTING_VISION_ROLE,
            LISTING_WEB_RESEARCH_ROLE,
        )
    )
    with pytest.raises(ValueError, match="web_search"):
        LISTING_WEB_RESEARCH_ROLE.bind(
            provider="vendor",
            model="text-only",
            capabilities=(ModelCapability.TEXT, ModelCapability.STRUCTURED_OUTPUT),
        )


def test_agent_roles_are_independent_from_listing_roles() -> None:
    assert AGENT_FAST_ROLE.role_id == "agent.fast"
    assert AGENT_REASONING_ROLE.role_id == "agent.reasoning"
    assert ModelCapability.TOOL_CALLING in AGENT_FAST_ROLE.required_capabilities
    assert ModelCapability.REASONING not in AGENT_FAST_ROLE.required_capabilities
    assert ModelCapability.REASONING in AGENT_REASONING_ROLE.required_capabilities
    assert AGENT_FAST_ROLE.allow_fallback is False
    assert AGENT_REASONING_ROLE.allow_fallback is False


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


def test_provider_catalog_binds_role_through_concrete_connection() -> None:
    catalog = ProviderCatalog()
    connection = ProviderConnection(
        provider_id="dashscope",
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        credential_ref=CredentialRef.environment("DASHSCOPE_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1/",
        display_name="DashScope",
    )
    catalog.register(connection)

    profile = catalog.bind(
        LISTING_IDENTITY_ROLE,
        provider_id="dashscope",
        model="qwen-model",
        capabilities=(
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.VISION,
        ),
    )

    assert profile.provider == "dashscope"
    assert profile.model == "qwen-model"
    assert profile.credential_ref == CredentialRef.environment("DASHSCOPE_API_KEY")
    assert connection.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert connection.as_safe_dict()["adapter"] == "openai-compatible"


def test_provider_connection_rejects_unsafe_or_invalid_endpoint_shapes() -> None:
    credential = CredentialRef.environment("VENDOR_API_KEY")
    with pytest.raises(ValueError, match="requires base_url"):
        ProviderConnection(
            provider_id="vendor",
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            credential_ref=credential,
        )
    with pytest.raises(ValueError, match="must not contain credentials"):
        ProviderConnection(
            provider_id="vendor",
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            credential_ref=credential,
            base_url="https://user:pass@api.vendor.test/v1",
        )
    with pytest.raises(ValueError, match="query parameters or fragments"):
        ProviderConnection(
            provider_id="vendor",
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            credential_ref=credential,
            base_url="https://api.vendor.test/v1?token=secret",
        )
    with pytest.raises(ValueError, match="does not accept base_url"):
        ProviderConnection(
            provider_id="openai",
            adapter=ProviderAdapter.OPENAI,
            credential_ref=CredentialRef.environment("OPENAI_API_KEY"),
            base_url="https://api.openai.com/v1",
        )


def test_reserved_provider_slots_exist_but_cannot_be_executed() -> None:
    assert provider_descriptor(ProviderAdapter.ANTHROPIC).executable is False
    assert provider_descriptor(ProviderAdapter.GEMINI).executable is False

    catalog = ProviderCatalog()
    catalog.register(
        ProviderConnection(
            provider_id="anthropic-main",
            adapter=ProviderAdapter.ANTHROPIC,
            credential_ref=CredentialRef.environment("ANTHROPIC_API_KEY"),
        )
    )
    with pytest.raises(RuntimeError, match="reserved but not executable"):
        catalog.bind(
            AGENT_REASONING_ROLE,
            provider_id="anthropic-main",
            model="future-model",
            capabilities=AGENT_REASONING_ROLE.required_capabilities,
        )


def test_provider_catalog_rejects_duplicate_connection_ids() -> None:
    catalog = ProviderCatalog()
    connection = ProviderConnection(
        provider_id="openai",
        adapter=ProviderAdapter.OPENAI,
        credential_ref=CredentialRef.environment("OPENAI_API_KEY"),
    )
    catalog.register(connection)
    with pytest.raises(ValueError, match="already registered"):
        catalog.register(connection)


def test_ai_platform_remains_detached_from_current_listing_production_path() -> None:
    registry_source = (
        Path(__file__).resolve().parents[1] / "app" / "providers" / "registry.py"
    ).read_text(encoding="utf-8")

    assert "AIPlatform" not in registry_source
    assert "listing_semantic_profile" not in registry_source
    assert "ProviderCatalog" not in registry_source
    assert "..ai" not in registry_source
