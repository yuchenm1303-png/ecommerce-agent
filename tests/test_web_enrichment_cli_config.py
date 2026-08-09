from __future__ import annotations

from argparse import Namespace

import makro_resolve_ai
from app.providers.registry import ProviderConfig


def _args(**overrides):
    values = {
        "web_enrich": "auto",
        "web_search_model": "qwen3.7-max",
        "web_base_url": "",
        "request_timeout_seconds": 90.0,
    }
    values.update(overrides)
    return Namespace(**values)


def test_auto_web_research_uses_dashscope_key_responses_model_endpoint_and_deadline(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "secret")
    config = ProviderConfig(
        provider="openai-compatible",
        model="qwen3.7-plus",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    provider, reason = makro_resolve_ai._dashscope_web_provider(_args(), config)
    assert reason == "available"
    assert provider is not None
    assert provider.model == "qwen3.7-max"
    assert provider.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert provider.request_timeout_seconds == 90.0


def test_auto_web_research_does_not_attach_to_other_compatible_endpoints(monkeypatch):
    monkeypatch.setenv("VENDOR_KEY", "secret")
    config = ProviderConfig(
        provider="openai-compatible",
        model="qwen3.7-plus",
        api_key_env="VENDOR_KEY",
        base_url="https://api.vendor.test/v1",
    )
    provider, reason = makro_resolve_ai._dashscope_web_provider(_args(), config)
    assert provider is None
    assert "not dashscope" in reason


def test_web_research_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "secret")
    config = ProviderConfig(
        provider="openai-compatible",
        model="qwen3.7-plus",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    provider, reason = makro_resolve_ai._dashscope_web_provider(_args(web_enrich="off"), config)
    assert provider is None
    assert reason == "disabled"
