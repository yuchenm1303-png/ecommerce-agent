from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai_relay_pool import (
    RelayConnection,
    RelayPoolProfile,
    RelayRoleBinding,
    load_relay_connection_key,
    load_relay_pool,
    relay_pool_path,
    save_relay_connection_key,
    save_relay_pool,
)
from app.providers.registry import ProviderConfigurationError


def _pool() -> RelayPoolProfile:
    return RelayPoolProfile(
        connections=(
            RelayConnection("relay-1", "连接 A", "https://a.example/v1"),
            RelayConnection("relay-2", "连接 B", "https://b.example/v1"),
            RelayConnection("relay-3", "连接 C", "https://c.example/v1"),
        ),
        semantic=RelayRoleBinding("relay-2", "vision-model"),
        fact=RelayRoleBinding("relay-1", "fact-model"),
        web=RelayRoleBinding("relay-3", "web-model"),
    )


def test_roles_can_bind_three_different_connections(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECOMMERCE_AGENT_CONFIG_DIR", str(tmp_path))
    saved = save_relay_pool(_pool())
    loaded = load_relay_pool()
    assert loaded == saved
    assert loaded is not None
    assert loaded.semantic.connection_id == "relay-2"
    assert loaded.fact.connection_id == "relay-1"
    assert loaded.web.connection_id == "relay-3"
    assert loaded.connection("relay-2").base_url == "https://b.example/v1"


def test_role_cannot_reference_disabled_or_missing_connection(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECOMMERCE_AGENT_CONFIG_DIR", str(tmp_path))
    profile = RelayPoolProfile(
        connections=(RelayConnection("relay-1", "连接 A", "https://a.example/v1"),),
        semantic=RelayRoleBinding("relay-1", "model-a"),
        fact=RelayRoleBinding("relay-2", "model-b"),
        web=RelayRoleBinding("relay-1", "model-c"),
    )
    with pytest.raises(ProviderConfigurationError, match="不存在"):
        save_relay_pool(profile)


def test_pool_file_contains_connections_and_role_bindings(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECOMMERCE_AGENT_CONFIG_DIR", str(tmp_path))
    save_relay_pool(_pool())
    payload = json.loads(relay_pool_path().read_text(encoding="utf-8"))
    assert [item["id"] for item in payload["connections"]] == ["relay-1", "relay-2", "relay-3"]
    assert payload["roles"]["semantic"] == {"connection_id": "relay-2", "model": "vision-model"}
    assert payload["roles"]["fact"] == {"connection_id": "relay-1", "model": "fact-model"}
    assert payload["roles"]["web"] == {"connection_id": "relay-3", "model": "web-model"}


def test_connection_urls_are_normalized(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECOMMERCE_AGENT_CONFIG_DIR", str(tmp_path))
    profile = RelayPoolProfile(
        connections=(RelayConnection("relay-1", "A", "https://relay.example/v1/"),),
        semantic=RelayRoleBinding("relay-1", "m1"),
        fact=RelayRoleBinding("relay-1", "m2"),
        web=RelayRoleBinding("relay-1", "m3"),
    )
    saved = save_relay_pool(profile)
    assert saved.connections[0].base_url == "https://relay.example/v1"


def test_invalid_connection_url_is_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECOMMERCE_AGENT_CONFIG_DIR", str(tmp_path))
    profile = RelayPoolProfile(
        connections=(RelayConnection("relay-1", "A", "not-a-url"),),
        semantic=RelayRoleBinding("relay-1", "m1"),
        fact=RelayRoleBinding("relay-1", "m2"),
        web=RelayRoleBinding("relay-1", "m3"),
    )
    with pytest.raises(ProviderConfigurationError, match="Base URL"):
        save_relay_pool(profile)


def test_connection_keys_are_independent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECOMMERCE_AGENT_CONFIG_DIR", str(tmp_path))
    # Source-development persistence is environment-backed on non-Windows;
    # each connection must still use an isolated credential slot.
    monkeypatch.delenv("ECOMMERCE_AGENT_AI_RELAY_API_KEY", raising=False)
    monkeypatch.delenv("ECOMMERCE_AGENT_AI_RELAY_2_API_KEY", raising=False)
    monkeypatch.delenv("ECOMMERCE_AGENT_AI_RELAY_3_API_KEY", raising=False)
    save_relay_connection_key("relay-1", "key-a")
    save_relay_connection_key("relay-2", "key-b")
    save_relay_connection_key("relay-3", "key-c")
    assert load_relay_connection_key("relay-1") == "key-a"
    assert load_relay_connection_key("relay-2") == "key-b"
    assert load_relay_connection_key("relay-3") == "key-c"
