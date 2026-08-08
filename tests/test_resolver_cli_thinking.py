from __future__ import annotations

import makro_resolve_ai
from app.providers.registry import ProviderConfig, validate_provider_config


def test_resolver_cli_exposes_explicit_thinking_switches():
    parser = makro_resolve_ai.build_parser()
    options = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--enable-thinking" in options
    assert "--disable-thinking" in options


def test_qwen_thinking_mode_changes_semantic_cache_namespace():
    base = dict(
        provider="openai-compatible",
        model="qwen3.5-omni-plus",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    fast = validate_provider_config(ProviderConfig(**base, enable_thinking=False))
    thinking = validate_provider_config(ProviderConfig(**base, enable_thinking=True))

    assert fast.enable_thinking is False
    assert thinking.enable_thinking is True
    assert makro_resolve_ai._semantic_cache_namespace(fast) != makro_resolve_ai._semantic_cache_namespace(thinking)
