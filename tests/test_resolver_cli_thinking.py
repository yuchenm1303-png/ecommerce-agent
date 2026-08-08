from __future__ import annotations

import pytest

import makro_resolve_ai
from app.providers.registry import ProviderConfig, ProviderConfigurationError, validate_provider_config


def test_resolver_cli_exposes_explicit_thinking_switches():
    parser = makro_resolve_ai.build_parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert "--enable-thinking" in options
    assert "--disable-thinking" in options


def test_qwen_auto_json_mode_disables_thinking():
    config = validate_provider_config(
        ProviderConfig(
            provider="openai-compatible",
            model="qwen3.5-omni-plus",
            api_key_env="DASHSCOPE_API_KEY",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    )
    assert config.structured_mode == "json_object"
    assert config.enable_thinking is False


def test_qwen_thinking_requires_explicit_prompt_only_mode_and_changes_cache_namespace():
    base = dict(
        provider="openai-compatible",
        model="qwen3.5-omni-plus",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        structured_mode="prompt_only",
    )
    fast = validate_provider_config(ProviderConfig(**base, enable_thinking=False))
    thinking = validate_provider_config(ProviderConfig(**base, enable_thinking=True))

    assert fast.enable_thinking is False
    assert thinking.enable_thinking is True
    assert makro_resolve_ai._cache_namespace(fast) != makro_resolve_ai._cache_namespace(thinking)


def test_qwen_json_mode_rejects_thinking_on():
    with pytest.raises(ProviderConfigurationError, match="JSON mode"):
        validate_provider_config(
            ProviderConfig(
                provider="openai-compatible",
                model="qwen3.5-omni-plus",
                api_key_env="DASHSCOPE_API_KEY",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                structured_mode="json_object",
                enable_thinking=True,
            )
        )
