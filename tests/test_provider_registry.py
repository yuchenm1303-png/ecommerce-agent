from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.providers.openai_compatible import OpenAICompatibleSemanticProvider
from app.providers.openai_semantic import OpenAISemanticProvider
from app.providers.registry import (
    ProviderConfig,
    ProviderConfigurationError,
    build_semantic_provider,
    default_api_key_env,
    resolve_api_key,
    validate_provider_config,
)


class DummyClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: None))


def test_default_api_key_env_is_provider_specific():
    assert default_api_key_env("openai") == "OPENAI_API_KEY"
    assert default_api_key_env("openai-compatible") == "AI_API_KEY"


def test_missing_key_fails_without_printing_secret():
    config = ProviderConfig(
        provider="openai-compatible",
        model="vision-model",
        api_key_env="VENDOR_KEY",
        base_url="https://api.vendor.test/v1",
    )
    with pytest.raises(ProviderConfigurationError, match="VENDOR_KEY 未设置"):
        resolve_api_key(config, environ={})


def test_openai_compatible_requires_base_url():
    with pytest.raises(ProviderConfigurationError, match="--base-url"):
        validate_provider_config(
            ProviderConfig(
                provider="openai-compatible",
                model="vision-model",
                api_key_env="VENDOR_KEY",
            )
        )


def test_invalid_base_url_is_rejected():
    with pytest.raises(ProviderConfigurationError, match="http/https"):
        validate_provider_config(
            ProviderConfig(
                provider="openai-compatible",
                model="vision-model",
                api_key_env="VENDOR_KEY",
                base_url="api.vendor.test/v1",
            )
        )


def test_provider_timeout_is_bounded():
    with pytest.raises(ProviderConfigurationError, match="10..600"):
        validate_provider_config(
            ProviderConfig(
                provider="openai-compatible",
                model="vision-model",
                api_key_env="VENDOR_KEY",
                base_url="https://api.vendor.test/v1",
                request_timeout_seconds=5,
            )
        )


def test_safe_config_does_not_log_url_credentials_or_query_tokens():
    config = ProviderConfig(
        provider="openai-compatible",
        model="vision-model",
        api_key_env="VENDOR_KEY",
        base_url="https://user:pass@api.vendor.test/v1?token=secret#fragment",
        request_timeout_seconds=90,
    )
    safe = config.as_safe_dict()

    assert safe["base_url"] == "https://api.vendor.test/v1"
    assert safe["request_timeout_seconds"] == 90
    assert safe["enable_thinking"] is None
    assert safe["sdk_max_retries"] == 0
    assert "pass" not in repr(safe)
    assert "secret" not in repr(safe)


def test_registry_builds_compatible_provider_from_arbitrary_env_name():
    config = ProviderConfig(
        provider="openai-compatible",
        model="vision-model",
        api_key_env="MY_VENDOR_API_KEY",
        base_url="https://api.vendor.test/v1",
        structured_mode="json_object",
        request_timeout_seconds=75,
    )
    provider = build_semantic_provider(
        config,
        environ={"MY_VENDOR_API_KEY": "secret"},
        client=DummyClient(),
    )

    assert isinstance(provider, OpenAICompatibleSemanticProvider)
    assert provider.model == "vision-model"
    assert provider.base_url == "https://api.vendor.test/v1"
    assert provider.structured_mode == "json_object"
    assert provider.compat_profile == "generic"
    assert provider.request_timeout_seconds == 75
    assert provider.enable_thinking is None


def test_qwen_omni_model_auto_selects_streaming_profile_and_disables_thinking():
    config = ProviderConfig(
        provider="openai-compatible",
        model="qwen3.5-omni-plus-2026-03-15",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    normalized = validate_provider_config(config)
    assert normalized.compat_profile == "qwen-omni"
    assert normalized.enable_thinking is False
    assert normalized.as_safe_dict()["compat_profile"] == "qwen-omni"
    assert normalized.as_safe_dict()["enable_thinking"] is False

    provider = build_semantic_provider(
        config,
        environ={"DASHSCOPE_API_KEY": "secret"},
        client=DummyClient(),
    )
    assert provider.compat_profile == "qwen-omni"
    assert provider.enable_thinking is False


def test_qwen_thinking_can_be_explicitly_reenabled():
    normalized = validate_provider_config(
        ProviderConfig(
            provider="openai-compatible",
            model="qwen3.5-omni-plus",
            api_key_env="DASHSCOPE_API_KEY",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            enable_thinking=True,
        )
    )
    assert normalized.compat_profile == "qwen-omni"
    assert normalized.enable_thinking is True


def test_native_openai_rejects_compatible_thinking_switch():
    with pytest.raises(ProviderConfigurationError, match="thinking"):
        validate_provider_config(
            ProviderConfig(
                provider="openai",
                model="gpt-5.6",
                api_key_env="OPENAI_API_KEY",
                enable_thinking=False,
            )
        )


def test_registry_keeps_native_openai_adapter_available():
    config = ProviderConfig(
        provider="openai",
        model="gpt-5.6",
        api_key_env="CUSTOM_OPENAI_KEY",
        request_timeout_seconds=45,
    )
    provider = build_semantic_provider(
        config,
        environ={"CUSTOM_OPENAI_KEY": "secret"},
        client=DummyClient(),
    )

    assert isinstance(provider, OpenAISemanticProvider)
    assert provider.model == "gpt-5.6"
    assert provider.request_timeout_seconds == 45


def test_unknown_provider_fails_closed():
    with pytest.raises(ProviderConfigurationError, match="不支持的 provider"):
        validate_provider_config(
            ProviderConfig(
                provider="mystery-ai",
                model="vision",
                api_key_env="KEY",
            )
        )
