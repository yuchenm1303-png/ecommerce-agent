from __future__ import annotations

import app.ai_service_settings as ai_settings
from app.ai_service_settings import (
    AIServiceSettings,
    CONFIG_DIR_ENV,
    RUNTIME_AI_KEY_ENV,
    load_ai_service_key,
    save_ai_service_settings,
)


def test_packaged_client_refuses_legacy_environment_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(RUNTIME_AI_KEY_ENV, raising=False)
    monkeypatch.setenv("AI_API_KEY", "developer-secret-must-not-leak")
    monkeypatch.setattr(ai_settings, "is_frozen", lambda: True)

    settings = save_ai_service_settings(AIServiceSettings(provider="openai-compatible"))
    assert load_ai_service_key(settings) == ""
