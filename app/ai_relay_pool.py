from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from .ai_profile_store import (
    load_relay_key as load_legacy_relay_key,
    load_relay_profile as load_legacy_relay_profile,
    relay_secret_path as legacy_relay_secret_path,
)
from .ai_service_settings import _atomic_write_text, _load_dpapi_key, _save_dpapi_key, settings_directory
from .providers.registry import ProviderConfigurationError


_POOL_FILENAME = "ai-relay-pool.json"
_POOL_VERSION = 1
_MAX_CONNECTIONS = 3
_CONNECTION_IDS = ("relay-1", "relay-2", "relay-3")
_RUNTIME_ENVS = {
    "relay-1": "ECOMMERCE_AGENT_AI_RELAY_API_KEY",
    "relay-2": "ECOMMERCE_AGENT_AI_RELAY_2_API_KEY",
    "relay-3": "ECOMMERCE_AGENT_AI_RELAY_3_API_KEY",
}
_SECRET_FILENAMES = {
    "relay-1": "ai-relay-key.dpapi",  # preserve the already-saved relay key
    "relay-2": "ai-relay-2-key.dpapi",
    "relay-3": "ai-relay-3-key.dpapi",
}


def _normalize_connection_id(value: str) -> str:
    connection_id = str(value or "").strip().casefold()
    if connection_id not in _CONNECTION_IDS:
        raise ProviderConfigurationError(f"未知中转连接：{value!r}")
    return connection_id


def _validate_url(value: str, *, label: str) -> str:
    base_url = str(value or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderConfigurationError(f"{label} Base URL 必须是完整的 http(s) 地址。")
    return base_url


@dataclass(slots=True, frozen=True)
class RelayConnection:
    connection_id: str
    name: str
    base_url: str

    def validated(self) -> "RelayConnection":
        connection_id = _normalize_connection_id(self.connection_id)
        name = str(self.name or "").strip() or connection_id
        base_url = _validate_url(self.base_url, label=name)
        return replace(self, connection_id=connection_id, name=name, base_url=base_url)

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.connection_id,
            "name": self.name,
            "base_url": self.base_url,
        }


@dataclass(slots=True, frozen=True)
class RelayRoleBinding:
    connection_id: str
    model: str

    def validated(self, *, role: str) -> "RelayRoleBinding":
        connection_id = _normalize_connection_id(self.connection_id)
        model = str(self.model or "").strip()
        if not model:
            raise ProviderConfigurationError(f"{role} 模型不能为空。")
        return replace(self, connection_id=connection_id, model=model)

    def as_dict(self) -> dict[str, str]:
        return {"connection_id": self.connection_id, "model": self.model}


@dataclass(slots=True, frozen=True)
class RelayPoolProfile:
    connections: tuple[RelayConnection, ...]
    semantic: RelayRoleBinding
    fact: RelayRoleBinding
    web: RelayRoleBinding

    def validated(self) -> "RelayPoolProfile":
        if not self.connections:
            raise ProviderConfigurationError("至少需要一条中转连接。")
        if len(self.connections) > _MAX_CONNECTIONS:
            raise ProviderConfigurationError(f"当前最多支持 {_MAX_CONNECTIONS} 条中转连接。")
        normalized_connections = tuple(item.validated() for item in self.connections)
        ids = [item.connection_id for item in normalized_connections]
        if len(set(ids)) != len(ids):
            raise ProviderConfigurationError("中转连接 ID 重复。")
        available = set(ids)
        semantic = self.semantic.validated(role="主语义")
        fact = self.fact.validated(role="Fact")
        web = self.web.validated(role="Web")
        for role, binding in (("主语义", semantic), ("Fact", fact), ("Web", web)):
            if binding.connection_id not in available:
                raise ProviderConfigurationError(f"{role} 选择了不存在的中转连接。")
        return replace(
            self,
            connections=normalized_connections,
            semantic=semantic,
            fact=fact,
            web=web,
        )

    def connection(self, connection_id: str) -> RelayConnection:
        normalized = _normalize_connection_id(connection_id)
        for item in self.connections:
            if item.connection_id == normalized:
                return item
        raise ProviderConfigurationError(f"中转连接不存在：{connection_id!r}")

    def binding_for(self, role: str) -> RelayRoleBinding:
        normalized = str(role or "").strip().casefold()
        if normalized == "semantic":
            return self.semantic
        if normalized == "fact":
            return self.fact
        if normalized == "web":
            return self.web
        raise ProviderConfigurationError(f"未知 AI 角色：{role!r}")

    def as_dict(self) -> dict[str, object]:
        return {
            "version": _POOL_VERSION,
            "connections": [item.as_dict() for item in self.connections],
            "roles": {
                "semantic": self.semantic.as_dict(),
                "fact": self.fact.as_dict(),
                "web": self.web.as_dict(),
            },
        }


def relay_pool_path() -> Path:
    return settings_directory() / _POOL_FILENAME


def relay_connection_secret_path(connection_id: str) -> Path:
    normalized = _normalize_connection_id(connection_id)
    # relay-1 deliberately reuses the old filename so existing saved credentials survive.
    if normalized == "relay-1":
        return legacy_relay_secret_path()
    return settings_directory() / _SECRET_FILENAMES[normalized]


def load_relay_connection_key(connection_id: str) -> str:
    normalized = _normalize_connection_id(connection_id)
    if normalized == "relay-1":
        try:
            key = load_legacy_relay_key()
        except Exception:
            key = ""
        if key:
            return key
    stored = _load_dpapi_key(
        relay_connection_secret_path(normalized),
        error_message=f"本机 {normalized} API Key 无法解密；请重新保存。",
    )
    if stored:
        return stored
    return str(os.getenv(_RUNTIME_ENVS[normalized], "") or "").strip()


def save_relay_connection_key(connection_id: str, api_key: str) -> None:
    normalized = _normalize_connection_id(connection_id)
    value = str(api_key or "").strip()
    if not value:
        raise ProviderConfigurationError("中转站 API Key 不能为空。")
    if os.name != "nt":
        os.environ[_RUNTIME_ENVS[normalized]] = value
        return
    _save_dpapi_key(relay_connection_secret_path(normalized), value)


def has_relay_connection_key(connection_id: str) -> bool:
    try:
        return bool(load_relay_connection_key(connection_id))
    except ProviderConfigurationError:
        return False


def clear_relay_connection_key(connection_id: str) -> None:
    normalized = _normalize_connection_id(connection_id)
    os.environ.pop(_RUNTIME_ENVS[normalized], None)
    try:
        relay_connection_secret_path(normalized).unlink()
    except FileNotFoundError:
        pass


def _legacy_profile_as_pool() -> RelayPoolProfile | None:
    legacy = load_legacy_relay_profile()
    if legacy is None:
        return None
    return RelayPoolProfile(
        connections=(RelayConnection("relay-1", "连接 A", legacy.base_url),),
        semantic=RelayRoleBinding("relay-1", legacy.model),
        fact=RelayRoleBinding("relay-1", legacy.fact_model),
        web=RelayRoleBinding("relay-1", legacy.web_model),
    ).validated()


def load_relay_pool() -> RelayPoolProfile | None:
    path = relay_pool_path()
    if not path.is_file():
        return _legacy_profile_as_pool()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProviderConfigurationError(f"中转连接池配置损坏：{path}") from exc
    if not isinstance(payload, dict) or int(payload.get("version", 0)) != _POOL_VERSION:
        raise ProviderConfigurationError("中转连接池配置版本不受支持。")
    raw_connections = payload.get("connections")
    roles = payload.get("roles")
    if not isinstance(raw_connections, list) or not isinstance(roles, dict):
        raise ProviderConfigurationError("中转连接池配置格式无效。")
    connections = tuple(
        RelayConnection(
            connection_id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
            base_url=str(item.get("base_url") or ""),
        )
        for item in raw_connections
        if isinstance(item, dict)
    )
    def binding(name: str) -> RelayRoleBinding:
        item = roles.get(name)
        if not isinstance(item, dict):
            raise ProviderConfigurationError(f"中转连接池缺少 {name} 角色绑定。")
        return RelayRoleBinding(
            connection_id=str(item.get("connection_id") or ""),
            model=str(item.get("model") or ""),
        )
    return RelayPoolProfile(
        connections=connections,
        semantic=binding("semantic"),
        fact=binding("fact"),
        web=binding("web"),
    ).validated()


def save_relay_pool(profile: RelayPoolProfile) -> RelayPoolProfile:
    normalized = profile.validated()
    _atomic_write_text(
        relay_pool_path(),
        json.dumps(normalized.as_dict(), ensure_ascii=False, indent=2) + "\n",
    )
    return normalized


__all__ = [
    "RelayConnection",
    "RelayPoolProfile",
    "RelayRoleBinding",
    "clear_relay_connection_key",
    "has_relay_connection_key",
    "load_relay_connection_key",
    "load_relay_pool",
    "relay_connection_secret_path",
    "relay_pool_path",
    "save_relay_connection_key",
    "save_relay_pool",
]
