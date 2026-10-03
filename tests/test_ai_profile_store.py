from __future__ import annotations

import json

from app.ai_profile_store import (
    AI_SOURCE_QWEN,
    AI_SOURCE_RELAY,
    RELAY_RUNTIME_KEY_ENV,
    RelayAIProfile,
    load_active_ai_source,
    load_relay_key,
    load_relay_profile,
    relay_profile_path,
    save_active_ai_source,
    save_relay_key,
    save_relay_profile,
)


def test_profile_source_defaults_to_qwen_and_switches_without_touching_relay(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ECOMMERCE_AGENT_CONFIG_DIR", str(tmp_path))
    assert load_active_ai_source() == AI_SOURCE_QWEN
    assert load_relay_profile() is None

    save_active_ai_source(AI_SOURCE_RELAY)
    assert load_active_ai_source() == AI_SOURCE_RELAY
    assert load_relay_profile() is None

    save_active_ai_source(AI_SOURCE_QWEN)
    assert load_active_ai_source() == AI_SOURCE_QWEN


def test_relay_profile_is_persisted_separately_from_legacy_qwen_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ECOMMERCE_AGENT_CONFIG_DIR", str(tmp_path))
    legacy = tmp_path / "ai-service.json"
    legacy.write_text('{"legacy":"qwen"}\n', encoding="utf-8")

    profile = RelayAIProfile(
        base_url="https://relay.example/v1/",
        model="semantic-model",
        fact_model="fact-model",
        web_model="web-model",
    )
    saved = save_relay_profile(profile)

    assert saved.base_url == "https://relay.example/v1"
    assert load_relay_profile() == saved
    assert json.loads(relay_profile_path().read_text(encoding="utf-8"))["model"] == "semantic-model"
    assert legacy.read_text(encoding="utf-8") == '{"legacy":"qwen"}\n'


def test_relay_key_uses_separate_runtime_slot_in_source_development(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ECOMMERCE_AGENT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv(RELAY_RUNTIME_KEY_ENV, raising=False)
    monkeypatch.setenv("ECOMMERCE_AGENT_AI_API_KEY", "qwen-key")

    save_relay_key("relay-key")

    assert load_relay_key() == "relay-key"
    assert __import__("os").environ["ECOMMERCE_AGENT_AI_API_KEY"] == "qwen-key"


def test_relay_profile_rejects_missing_role_models(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ECOMMERCE_AGENT_CONFIG_DIR", str(tmp_path))
    profile = RelayAIProfile(
        base_url="https://relay.example/v1",
        model="semantic-model",
        fact_model="",
        web_model="web-model",
    )
    try:
        save_relay_profile(profile)
    except Exception as exc:
        assert "Fact" in str(exc)
    else:
        raise AssertionError("missing Fact model should not be accepted")
