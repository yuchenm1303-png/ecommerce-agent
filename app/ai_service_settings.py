from __future__ import annotations

import base64
import ctypes
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .providers.registry import ProviderConfig, ProviderConfigurationError, validate_provider_config
from .runtime_paths import is_frozen


CONFIG_DIR_ENV = "ECOMMERCE_AGENT_CONFIG_DIR"
RUNTIME_AI_KEY_ENV = "ECOMMERCE_AGENT_AI_API_KEY"
_CONFIG_FILENAME = "ai-service.json"
_SECRET_FILENAME = "ai-service-key.dpapi"
_CONFIG_VERSION = 1


@dataclass(slots=True, frozen=True)
class AIServiceSettings:
    provider: str = "openai-compatible"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen3.7-plus"
    fact_model: str = "qwen3.7-max"
    web_model: str = "qwen3.7-max"

    def validated(self) -> "AIServiceSettings":
        provider = str(self.provider or "").strip().casefold()
        base_url = str(self.base_url or "").strip()
        model = str(self.model or "").strip()
        fact_model = str(self.fact_model or "").strip()
        web_model = str(self.web_model or "").strip()
        if not fact_model:
            raise ProviderConfigurationError("事实模型不能为空。")
        if not web_model:
            raise ProviderConfigurationError("Web 搜索模型不能为空。")
        validated = validate_provider_config(
            ProviderConfig(
                provider=provider,
                model=model,
                api_key_env=RUNTIME_AI_KEY_ENV,
                base_url=base_url,
                structured_mode="json_object",
                enable_thinking=False if provider == "openai-compatible" else None,
            )
        )
        return replace(
            self,
            provider=validated.provider,
            base_url=validated.base_url,
            model=validated.model,
            fact_model=fact_model,
            web_model=web_model,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": _CONFIG_VERSION,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "fact_model": self.fact_model,
            "web_model": self.web_model,
        }


def settings_directory() -> Path:
    override = str(os.getenv(CONFIG_DIR_ENV, "") or "").strip()
    if override:
        root = Path(override).expanduser()
    elif os.name == "nt":
        local_app_data = str(os.getenv("LOCALAPPDATA", "") or "").strip()
        root = (
            Path(local_app_data) / "EcommerceAgent" / "settings"
            if local_app_data
            else Path.home() / "AppData" / "Local" / "EcommerceAgent" / "settings"
        )
    else:
        xdg = str(os.getenv("XDG_CONFIG_HOME", "") or "").strip()
        root = (Path(xdg) if xdg else Path.home() / ".config") / "ecommerce-agent"
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _config_path() -> Path:
    return settings_directory() / _CONFIG_FILENAME


def _secret_path() -> Path:
    return settings_directory() / _SECRET_FILENAME


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def load_ai_service_settings() -> AIServiceSettings:
    path = _config_path()
    if not path.is_file():
        return AIServiceSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProviderConfigurationError(f"AI 服务设置损坏：{path}") from exc
    if not isinstance(payload, dict):
        raise ProviderConfigurationError(f"AI 服务设置格式无效：{path}")
    settings = AIServiceSettings(
        provider=str(payload.get("provider") or "openai-compatible"),
        base_url=str(payload.get("base_url") or ""),
        model=str(payload.get("model") or ""),
        fact_model=str(payload.get("fact_model") or ""),
        web_model=str(payload.get("web_model") or ""),
    )
    return settings.validated()


def save_ai_service_settings(settings: AIServiceSettings, *, api_key: str | None = None) -> AIServiceSettings:
    normalized = settings.validated()
    _atomic_write_text(
        _config_path(),
        json.dumps(normalized.as_dict(), ensure_ascii=False, indent=2) + "\n",
    )
    if api_key is not None:
        value = str(api_key or "").strip()
        if not value:
            raise ProviderConfigurationError("API Key 不能为空；如需删除请使用清除密钥。")
        save_ai_service_key(value)
    return normalized


def _legacy_key_env(provider: str) -> str:
    return "OPENAI_API_KEY" if str(provider or "").strip().casefold() == "openai" else "AI_API_KEY"


def load_ai_service_key(settings: AIServiceSettings | None = None) -> str:
    configured = settings or load_ai_service_settings()
    path = _secret_path()
    if path.is_file() and os.name == "nt":
        try:
            encoded = path.read_text(encoding="ascii").strip()
            if encoded:
                return _dpapi_unprotect(base64.b64decode(encoded.encode("ascii"))).decode("utf-8").strip()
        except Exception as exc:
            raise ProviderConfigurationError("本机 AI API Key 无法解密；请在设置页重新保存。") from exc

    runtime = str(os.getenv(RUNTIME_AI_KEY_ENV, "") or "").strip()
    if runtime:
        return runtime

    # Packaged clients must be explicit BYOK. Never let a developer/service
    # environment variable silently become the customer's runtime credential.
    if is_frozen():
        return ""

    # Source development keeps the historical environment-variable workflow so
    # existing local tests and developer terminals do not need migration first.
    return str(os.getenv(_legacy_key_env(configured.provider), "") or "").strip()


def save_ai_service_key(api_key: str) -> None:
    value = str(api_key or "").strip()
    if not value:
        raise ProviderConfigurationError("API Key 不能为空。")
    if os.name != "nt":
        # Development fallback only. Production Windows builds persist through DPAPI.
        os.environ[RUNTIME_AI_KEY_ENV] = value
        return
    encrypted = _dpapi_protect(value.encode("utf-8"))
    _atomic_write_text(_secret_path(), base64.b64encode(encrypted).decode("ascii") + "\n")


def clear_ai_service_key() -> None:
    os.environ.pop(RUNTIME_AI_KEY_ENV, None)
    path = _secret_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def has_ai_service_key(settings: AIServiceSettings | None = None) -> bool:
    try:
        return bool(load_ai_service_key(settings))
    except ProviderConfigurationError:
        return False


def resolved_ai_runtime() -> tuple[AIServiceSettings, str]:
    settings = load_ai_service_settings().validated()
    api_key = load_ai_service_key(settings)
    if not api_key:
        raise ProviderConfigurationError(
            "尚未配置 AI API Key。请打开“设置 → AI 服务”，填写你自己的服务商 API Key 后再运行。"
        )
    return settings, api_key


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob_from_bytes(data: bytes) -> tuple[_DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DATA_BLOB(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is unavailable on this platform")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _buffer = _blob_from_bytes(data)
    out_blob = _DATA_BLOB()
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "ecommerce-agent AI API key",
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is unavailable on this platform")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _buffer = _blob_from_bytes(data)
    out_blob = _DATA_BLOB()
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


__all__ = [
    "AIServiceSettings",
    "CONFIG_DIR_ENV",
    "RUNTIME_AI_KEY_ENV",
    "clear_ai_service_key",
    "has_ai_service_key",
    "load_ai_service_key",
    "load_ai_service_settings",
    "resolved_ai_runtime",
    "save_ai_service_key",
    "save_ai_service_settings",
    "settings_directory",
]
