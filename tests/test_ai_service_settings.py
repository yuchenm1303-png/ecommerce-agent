from __future__ import annotations

import json

from app.ai_service_settings import (
    AIServiceSettings,
    CONFIG_DIR_ENV,
    RUNTIME_AI_KEY_ENV,
    load_ai_service_key,
    load_ai_service_settings,
    save_ai_service_settings,
    settings_directory,
)


def test_non_secret_ai_settings_roundtrip_never_serializes_api_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(RUNTIME_AI_KEY_ENV, raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    saved = save_ai_service_settings(
        AIServiceSettings(
            provider="openai-compatible",
            base_url="https://example.invalid/compatible/v1",
            model="main-model",
            fact_model="fact-model",
            web_model="web-model",
        )
    )
    loaded = load_ai_service_settings()

    assert loaded == saved
    payload = json.loads((settings_directory() / "ai-service.json").read_text(encoding="utf-8"))
    assert payload["base_url"] == "https://example.invalid/compatible/v1"
    assert "api_key" not in payload
    assert "secret" not in payload


def test_runtime_key_can_be_supplied_process_locally_without_command_storage(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(RUNTIME_AI_KEY_ENV, "user-owned-secret")

    settings = save_ai_service_settings(AIServiceSettings())
    assert load_ai_service_key(settings) == "user-owned-secret"
    assert not (settings_directory() / "ai-service.json").read_text(encoding="utf-8").find(
        "user-owned-secret"
    ) >= 0


def test_legacy_development_env_remains_backward_compatible(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(RUNTIME_AI_KEY_ENV, raising=False)
    monkeypatch.setenv("AI_API_KEY", "legacy-dev-secret")

    settings = save_ai_service_settings(AIServiceSettings(provider="openai-compatible"))
    assert load_ai_service_key(settings) == "legacy-dev-secret"
