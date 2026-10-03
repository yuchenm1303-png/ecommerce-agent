from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ai_service_settings import (
    CONFIG_DIR_ENV,
    RUNTIME_AI_KEY_ENV,
    RUNTIME_FACT_BASE_URL_ENV,
    RUNTIME_FACT_KEY_ENV,
    RUNTIME_WEB_BASE_URL_ENV,
    RUNTIME_WEB_KEY_ENV,
    AIServiceSettings,
    load_ai_service_settings,
    resolved_ai_runtime,
)
from app.providers.registry import ProviderConfig, ProviderConfigurationError, validate_provider_config
from app.resolver_pipeline import dashscope_web_provider, fact_provider_config


def _provider(*, base_url: str, api_key_env: str = "AI_API_KEY") -> ProviderConfig:
    return validate_provider_config(
        ProviderConfig(
            provider="openai-compatible",
            model="main-model",
            api_key_env=api_key_env,
            base_url=base_url,
            structured_mode="json_object",
            enable_thinking=False,
        )
    )


def _web_args(**overrides):
    values = {
        "web_enrich": "auto",
        "web_search_model": "web-model",
        "web_base_url": "",
        "request_timeout_seconds": 120.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_legacy_settings_load_without_role_migration(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(RUNTIME_AI_KEY_ENV, "legacy-runtime-key")
    payload = {
        "version": 1,
        "provider": "openai-compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.7-plus",
        "fact_model": "qwen3.7-max",
        "web_model": "qwen3.7-max",
    }
    (tmp_path / "ai-service.json").write_text(json.dumps(payload), encoding="utf-8")

    settings = load_ai_service_settings()
    resolved, api_key = resolved_ai_runtime()

    assert settings.fact_override_enabled is False
    assert settings.fact_base_url == ""
    assert settings.web_override_enabled is False
    assert settings.web_base_url == ""
    assert resolved == settings
    assert api_key == "legacy-runtime-key"
    assert "api_key" not in settings.as_dict()
    assert "fact_api_key" not in settings.as_dict()
    assert "web_api_key" not in settings.as_dict()


def test_role_override_url_is_required_only_when_enabled() -> None:
    disabled = AIServiceSettings(fact_override_enabled=False, web_override_enabled=False).validated()
    assert disabled.fact_base_url == ""
    assert disabled.web_base_url == ""

    with pytest.raises(ProviderConfigurationError, match="Fact 独立连接"):
        AIServiceSettings(fact_override_enabled=True, fact_base_url="").validated()
    with pytest.raises(ProviderConfigurationError, match="Web 独立连接"):
        AIServiceSettings(web_override_enabled=True, web_base_url="not-a-url").validated()


def test_fact_provider_keeps_legacy_connection_without_override(monkeypatch) -> None:
    monkeypatch.delenv(RUNTIME_FACT_BASE_URL_ENV, raising=False)
    monkeypatch.delenv(RUNTIME_FACT_KEY_ENV, raising=False)
    main = _provider(base_url="https://main.example/v1", api_key_env="LEGACY_KEY")

    fact = fact_provider_config(SimpleNamespace(fact_model="fact-model"), main)

    assert fact.model == "fact-model"
    assert fact.base_url == main.base_url
    assert fact.api_key_env == "LEGACY_KEY"


def test_fact_provider_uses_explicit_role_connection(monkeypatch) -> None:
    monkeypatch.setenv(RUNTIME_FACT_BASE_URL_ENV, "https://fact-proxy.example/v1/")
    monkeypatch.setenv(RUNTIME_FACT_KEY_ENV, "fact-secret")
    main = _provider(base_url="https://main.example/v1", api_key_env="LEGACY_KEY")

    fact = fact_provider_config(SimpleNamespace(fact_model="fact-model"), main)

    assert fact.model == "fact-model"
    assert fact.base_url == "https://fact-proxy.example/v1"
    assert fact.api_key_env == RUNTIME_FACT_KEY_ENV


def test_web_provider_preserves_legacy_dashscope_gate(monkeypatch) -> None:
    monkeypatch.delenv(RUNTIME_WEB_BASE_URL_ENV, raising=False)
    monkeypatch.delenv(RUNTIME_WEB_KEY_ENV, raising=False)
    monkeypatch.setenv("LEGACY_KEY", "legacy-secret")
    generic_main = _provider(base_url="https://generic-proxy.example/v1", api_key_env="LEGACY_KEY")

    provider, availability = dashscope_web_provider(_web_args(), generic_main)

    assert provider is None
    assert availability == "current compatible endpoint is not dashscope.aliyuncs.com"


def test_web_provider_uses_only_explicit_dashscope_compatible_override(monkeypatch) -> None:
    monkeypatch.setenv(RUNTIME_WEB_BASE_URL_ENV, "https://web-proxy.example/responses/")
    monkeypatch.setenv(RUNTIME_WEB_KEY_ENV, "web-secret")
    generic_main = _provider(base_url="https://generic-proxy.example/v1", api_key_env="LEGACY_KEY")

    provider, availability = dashscope_web_provider(_web_args(), generic_main)

    assert availability == "available"
    assert provider is not None
    assert provider.base_url == "https://web-proxy.example/responses"
    assert provider.model == "web-model"
    assert provider.api_key == "web-secret"


def test_web_override_fails_closed_when_role_key_is_missing(monkeypatch) -> None:
    monkeypatch.setenv(RUNTIME_WEB_BASE_URL_ENV, "https://web-proxy.example/responses")
    monkeypatch.delenv(RUNTIME_WEB_KEY_ENV, raising=False)
    main = _provider(base_url="https://generic-proxy.example/v1", api_key_env="LEGACY_KEY")

    provider, availability = dashscope_web_provider(_web_args(), main)

    assert provider is None
    assert availability == f"missing API key env {RUNTIME_WEB_KEY_ENV}"
