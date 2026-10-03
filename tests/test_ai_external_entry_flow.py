from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.ai_connection_probe import (
    CapabilityCheck, CapabilityProbeError, RoleBinding, RoleCapabilityReport,
    save_verification_snapshot,
)
from app.ai_profile_store import (
    relay_verification_directory, save_active_ai_source,
)
from app.ai_relay_pool import (
    RelayConnection, RelayPoolProfile, RelayRoleBinding,
    load_relay_pool, save_relay_connection_key, save_relay_pool,
)
from app.ai_service_settings import AIServiceSettings, save_ai_service_settings, save_ai_service_key
from gui.ai_settings_pool_surface import AISettingsModalController


def prepared(monkeypatch, tmp_path):
    monkeypatch.setenv("ECOMMERCE_AGENT_CONFIG_DIR", str(tmp_path))
    for name in ("AI_API_KEY", "ECOMMERCE_AGENT_AI_FACT_API_KEY", "ECOMMERCE_AGENT_AI_WEB_API_KEY",
                 "ECOMMERCE_AGENT_AI_FACT_BASE_URL", "ECOMMERCE_AGENT_AI_WEB_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    save_ai_service_settings(AIServiceSettings())
    save_ai_service_key("fixture-qwen-key")
    connections = tuple(RelayConnection(f"relay-{i}", str(i), f"https://relay{i}.example/v1") for i in (1, 2, 3))
    pool = RelayPoolProfile(connections, RelayRoleBinding("relay-1", "semantic-model"),
                           RelayRoleBinding("relay-2", "fact-model"), RelayRoleBinding("relay-3", "web-model"))
    save_relay_pool(pool)
    roles = ("semantic", "fact", "web")
    bindings = []
    reports = []
    for i, role in enumerate(roles, 1):
        key = f"fixture-key-{i}"
        save_relay_connection_key(f"relay-{i}", key)
        binding = RoleBinding(role, connections[i-1].base_url, pool.binding_for(role).model, key)
        bindings.append(binding)
        names = {"semantic": ("chat_completions", "strict_json_schema", "vision"),
                 "fact": ("chat_completions", "strict_json_schema"),
                 "web": ("responses_api", "web_search", "web_sources")}[role]
        reports.append(RoleCapabilityReport(role, binding.base_url, binding.model, True,
                                            tuple(CapabilityCheck(name, True) for name in names)))
    return pool, bindings, reports


def apply_runtime():
    owner = SimpleNamespace(_clear_role_runtime=AISettingsModalController._clear_role_runtime)
    config = SimpleNamespace()
    AISettingsModalController._apply_runtime(owner, config)
    return config


def test_verified_pool_routes_roles_and_switches_back_to_qwen(monkeypatch, tmp_path):
    import os
    pool, bindings, reports = prepared(monkeypatch, tmp_path)
    qwen_before = (tmp_path / "ai-service.json").read_bytes()
    save_verification_snapshot(config_dir=relay_verification_directory(), bindings=bindings, reports=reports)
    save_active_ai_source("relay")
    config = apply_runtime()
    assert config.base_url == "https://relay1.example/v1"
    assert (config.local_model, config.fact_model, config.web_model) == ("semantic-model", "fact-model", "web-model")
    assert os.environ["AI_API_KEY"] == "fixture-key-1"
    assert os.environ["ECOMMERCE_AGENT_AI_FACT_API_KEY"] == "fixture-key-2"
    assert os.environ["ECOMMERCE_AGENT_AI_WEB_BASE_URL"] == "https://relay3.example/v1"
    save_active_ai_source("qwen")
    config = apply_runtime()
    assert config.base_url == AIServiceSettings().base_url
    assert os.environ["AI_API_KEY"] == "fixture-qwen-key"
    assert "ECOMMERCE_AGENT_AI_FACT_API_KEY" not in os.environ
    assert "ECOMMERCE_AGENT_AI_WEB_BASE_URL" not in os.environ
    assert (tmp_path / "ai-service.json").read_bytes() == qwen_before
    assert load_relay_pool() == pool


@pytest.mark.parametrize("change", ["model", "url", "key"])
def test_changed_external_binding_cannot_enter_runtime(monkeypatch, tmp_path, change):
    pool, bindings, reports = prepared(monkeypatch, tmp_path)
    save_verification_snapshot(config_dir=relay_verification_directory(), bindings=bindings, reports=reports)
    save_active_ai_source("relay")
    if change == "model":
        save_relay_pool(replace(pool, semantic=RelayRoleBinding("relay-1", "other-model")))
    elif change == "url":
        save_relay_pool(replace(pool, connections=(replace(pool.connections[0], base_url="https://changed.example/v1"), *pool.connections[1:])))
    else:
        save_relay_connection_key("relay-1", "changed-fixture-key")
    with pytest.raises(CapabilityProbeError, match="已经变化"):
        apply_runtime()


def test_unverified_pool_cannot_enter_runtime(monkeypatch, tmp_path):
    prepared(monkeypatch, tmp_path)
    save_active_ai_source("relay")
    with pytest.raises(CapabilityProbeError, match="缺少能力验证记录"):
        apply_runtime()


def test_failed_role_cannot_be_saved_as_verified(monkeypatch, tmp_path):
    pool, bindings, reports = prepared(monkeypatch, tmp_path)
    reports[-1] = replace(reports[-1], passed=False, error="search unsupported")
    with pytest.raises(CapabilityProbeError, match="不能保存"):
        save_verification_snapshot(config_dir=relay_verification_directory(), bindings=bindings, reports=reports)
