from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from .ai_service_settings import (
    AIServiceSettings,
    _atomic_write_text,
    _load_dpapi_key,
    _save_dpapi_key,
    load_ai_service_key,
    load_ai_service_settings,
    settings_directory,
)
from .providers.registry import ProviderConfigurationError


AI_SOURCE_QWEN = "qwen"
AI_SOURCE_RELAY = "relay"
RELAY_RUNTIME_KEY_ENV = "ECOMMERCE_AGENT_AI_RELAY_API_KEY"
_RELAY_PROFILE_FILENAME = "ai-relay-profile.json"
_RELAY_SECRET_FILENAME = "ai-relay-key.dpapi"
_ACTIVE_SOURCE_FILENAME = "ai-active-source.json"
_RELAY_VERIFICATION_DIRNAME = "ai-relay-verification"
_RELAY_PROFILE_VERSION = 1


@dataclass(slots=True, frozen=True)
class RelayAIProfile:
    base_url: str = ""
    model: str = ""
    fact_model: str = ""
    web_model: str = ""

    def validated(self) -> "RelayAIProfile":
        base_url = str(self.base_url or "").strip().rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderConfigurationError("中转站 Base URL 必须是完整的 http(s) 地址。")
        model = str(self.model or "").strip()
        fact_model = str(self.fact_model or "").strip()
        web_model = str(self.web_model or "").strip()
        if not model:
            raise ProviderConfigurationError("中转站主语义模型不能为空。")
        if not fact_model:
            raise ProviderConfigurationError("中转站 Fact 模型不能为空。")
        if not web_model:
            raise ProviderConfigurationError("中转站 Web 模型不能为空。")
        return replace(
            self,
            base_url=base_url,
            model=model,
            fact_model=fact_model,
            web_model=web_model,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "version": _RELAY_PROFILE_VERSION,
            "base_url": self.base_url,
            "model": self.model,
            "fact_model": self.fact_model,
            "web_model": self.web_model,
        }


def relay_profile_path() -> Path:
    return settings_directory() / _RELAY_PROFILE_FILENAME


def relay_secret_path() -> Path:
    return settings_directory() / _RELAY_SECRET_FILENAME


def relay_verification_directory() -> Path:
    path = settings_directory() / _RELAY_VERIFICATION_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def active_source_path() -> Path:
    return settings_directory() / _ACTIVE_SOURCE_FILENAME


def load_active_ai_source() -> str:
    path = active_source_path()
    if not path.is_file():
        return AI_SOURCE_QWEN
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        source = str(payload.get("source") or "").strip().casefold()
    except Exception as exc:
        raise ProviderConfigurationError(f"AI 来源设置损坏：{path}") from exc
    if source not in {AI_SOURCE_QWEN, AI_SOURCE_RELAY}:
        raise ProviderConfigurationError(f"未知 AI 来源：{source!r}")
    return source


def save_active_ai_source(source: str) -> str:
    normalized = str(source or "").strip().casefold()
    if normalized not in {AI_SOURCE_QWEN, AI_SOURCE_RELAY}:
        raise ProviderConfigurationError(f"未知 AI 来源：{source!r}")
    _atomic_write_text(
        active_source_path(),
        json.dumps({"version": 1, "source": normalized}, ensure_ascii=False, indent=2) + "\n",
    )
    return normalized


def load_relay_profile() -> RelayAIProfile | None:
    path = relay_profile_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProviderConfigurationError(f"中转站 AI 配置损坏：{path}") from exc
    if not isinstance(payload, dict):
        raise ProviderConfigurationError(f"中转站 AI 配置格式无效：{path}")
    if int(payload.get("version", 0)) != _RELAY_PROFILE_VERSION:
        raise ProviderConfigurationError("中转站 AI 配置版本不受支持。")
    return RelayAIProfile(
        base_url=str(payload.get("base_url") or ""),
        model=str(payload.get("model") or ""),
        fact_model=str(payload.get("fact_model") or ""),
        web_model=str(payload.get("web_model") or ""),
    ).validated()


def save_relay_profile(profile: RelayAIProfile) -> RelayAIProfile:
    normalized = profile.validated()
    _atomic_write_text(
        relay_profile_path(),
        json.dumps(normalized.as_dict(), ensure_ascii=False, indent=2) + "\n",
    )
    return normalized


def load_relay_key() -> str:
    stored = _load_dpapi_key(
        relay_secret_path(),
        error_message="本机中转站 API Key 无法解密；请在 AI 服务设置中重新保存。",
    )
    if stored:
        return stored
    return str(os.getenv(RELAY_RUNTIME_KEY_ENV, "") or "").strip()


def save_relay_key(api_key: str) -> None:
    value = str(api_key or "").strip()
    if not value:
        raise ProviderConfigurationError("中转站 API Key 不能为空。")
    if os.name != "nt":
        os.environ[RELAY_RUNTIME_KEY_ENV] = value
        return
    _save_dpapi_key(relay_secret_path(), value)


def clear_relay_key() -> None:
    os.environ.pop(RELAY_RUNTIME_KEY_ENV, None)
    try:
        relay_secret_path().unlink()
    except FileNotFoundError:
        pass


def has_relay_key() -> bool:
    try:
        return bool(load_relay_key())
    except ProviderConfigurationError:
        return False


def load_qwen_profile() -> tuple[AIServiceSettings, str]:
    """Return the existing proven Qwen profile without mutating or migrating it."""
    settings = load_ai_service_settings().validated()
    key = load_ai_service_key(settings)
    if not key:
        raise ProviderConfigurationError("官方 Qwen API Key 尚未配置。")
    return settings, key


__all__ = [
    "AI_SOURCE_QWEN",
    "AI_SOURCE_RELAY",
    "RELAY_RUNTIME_KEY_ENV",
    "RelayAIProfile",
    "active_source_path",
    "clear_relay_key",
    "has_relay_key",
    "load_active_ai_source",
    "load_qwen_profile",
    "load_relay_key",
    "load_relay_profile",
    "relay_profile_path",
    "relay_secret_path",
    "relay_verification_directory",
    "save_active_ai_source",
    "save_relay_key",
    "save_relay_profile",
]
