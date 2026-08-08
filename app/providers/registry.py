from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .openai_compatible import (
    SUPPORTED_COMPAT_PROFILES,
    OpenAICompatibleSemanticProvider,
)
from .openai_semantic import OpenAISemanticProvider


class ProviderConfigurationError(ValueError):
    pass


SUPPORTED_PROVIDERS = ("openai", "openai-compatible")


def _validated_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderConfigurationError("--base-url 必须是完整 http/https API 根地址。")
    return url


def _safe_base_url(value: str) -> str:
    """Strip credentials/query/fragment before writing provider metadata to logs."""

    if not value:
        return ""
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _effective_compat_profile(provider: str, model: str, requested: str) -> str:
    if provider != "openai-compatible" or requested != "generic":
        return requested
    normalized_model = model.casefold()
    if normalized_model.startswith(("qwen3.5-omni-", "qwen3-omni-", "qwen-omni-")):
        return "qwen-omni"
    return requested


def _effective_thinking(
    provider: str,
    compat_profile: str,
    requested: bool | None,
) -> bool | None:
    if provider != "openai-compatible":
        if requested is not None:
            raise ProviderConfigurationError(
                "thinking 开关当前仅用于 openai-compatible provider。"
            )
        return None
    if requested is not None:
        return bool(requested)
    # Qwen3.5 hybrid-thinking models default to thinking on at the service.
    # Semantic extraction is constrained information extraction, not open-ended
    # reasoning, so qwen-omni defaults to thinking off for predictable latency.
    if compat_profile == "qwen-omni":
        return False
    return None


@dataclass(slots=True, frozen=True)
class ProviderConfig:
    provider: str
    model: str
    api_key_env: str
    base_url: str = ""
    image_detail: str = "auto"
    max_output_tokens: int = 12000
    structured_mode: str = "prompt_only"
    compat_profile: str = "generic"
    request_timeout_seconds: float = 120.0
    enable_thinking: bool | None = None

    def as_safe_dict(self) -> dict[str, Any]:
        """Return audit metadata without exposing secret values."""

        return {
            "provider": self.provider,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "base_url": _safe_base_url(self.base_url),
            "image_detail": self.image_detail,
            "max_output_tokens": self.max_output_tokens,
            "structured_mode": self.structured_mode,
            "compat_profile": self.compat_profile,
            "request_timeout_seconds": self.request_timeout_seconds,
            "enable_thinking": self.enable_thinking,
            # Production-created SDK clients use max_retries=0. Retries are
            # explicit at the semantic source layer so latency/reporting stays
            # observable instead of being multiplied invisibly inside the SDK.
            "sdk_max_retries": 0,
        }


def default_api_key_env(provider: str) -> str:
    normalized = provider.strip().casefold()
    if normalized == "openai":
        return "OPENAI_API_KEY"
    if normalized == "openai-compatible":
        return "AI_API_KEY"
    raise ProviderConfigurationError(
        f"不支持的 provider={provider!r}；当前支持：{', '.join(SUPPORTED_PROVIDERS)}"
    )


def resolve_api_key(config: ProviderConfig, *, environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    value = str(env.get(config.api_key_env) or "").strip()
    if not value:
        raise ProviderConfigurationError(
            f"环境变量 {config.api_key_env} 未设置。请把当前 AI 服务的 API key 放入该环境变量；"
            "不要把 key 写进命令行参数、代码或仓库。"
        )
    return value


def validate_provider_config(config: ProviderConfig) -> ProviderConfig:
    provider = config.provider.strip().casefold()
    if provider not in SUPPORTED_PROVIDERS:
        raise ProviderConfigurationError(
            f"不支持的 provider={config.provider!r}；当前支持：{', '.join(SUPPORTED_PROVIDERS)}"
        )
    model = config.model.strip()
    if not model:
        raise ProviderConfigurationError("--model 不能为空。")
    if not config.api_key_env.strip():
        raise ProviderConfigurationError("--api-key-env 不能为空。")

    base_url = _validated_base_url(config.base_url)
    if provider == "openai-compatible" and not base_url:
        raise ProviderConfigurationError(
            "openai-compatible provider 必须提供 --base-url，例如服务商文档给出的 OpenAI-compatible API 根地址。"
        )
    if config.image_detail not in {"auto", "low", "high"}:
        raise ProviderConfigurationError("--image-detail 必须是 auto/low/high。")
    if config.max_output_tokens < 1000:
        raise ProviderConfigurationError("--max-output-tokens 不能小于 1000。")
    if config.structured_mode not in {"prompt_only", "json_object"}:
        raise ProviderConfigurationError(
            "--structured-mode 必须是 prompt_only/json_object。"
        )
    if config.compat_profile not in SUPPORTED_COMPAT_PROFILES:
        raise ProviderConfigurationError(
            "--compat-profile 必须是 " + "/".join(SUPPORTED_COMPAT_PROFILES) + "。"
        )
    if provider == "openai" and config.compat_profile != "generic":
        raise ProviderConfigurationError(
            "--compat-profile 仅用于 openai-compatible provider；原生 OpenAI 请使用 generic。"
        )
    try:
        timeout = float(config.request_timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ProviderConfigurationError("--request-timeout-seconds 必须是数字。") from exc
    if not 10.0 <= timeout <= 600.0:
        raise ProviderConfigurationError(
            "--request-timeout-seconds 必须在 10..600 秒；避免单个 source 无限等待。"
        )

    effective_profile = _effective_compat_profile(provider, model, config.compat_profile)
    effective_thinking = _effective_thinking(
        provider,
        effective_profile,
        config.enable_thinking,
    )
    return ProviderConfig(
        provider=provider,
        model=model,
        api_key_env=config.api_key_env.strip(),
        base_url=base_url,
        image_detail=config.image_detail,
        max_output_tokens=int(config.max_output_tokens),
        structured_mode=config.structured_mode,
        compat_profile=effective_profile,
        request_timeout_seconds=timeout,
        enable_thinking=effective_thinking,
    )


def build_semantic_provider(
    config: ProviderConfig,
    *,
    environ: dict[str, str] | None = None,
    client: Any | None = None,
):
    normalized = validate_provider_config(config)
    api_key = resolve_api_key(normalized, environ=environ)

    if normalized.provider == "openai-compatible":
        return OpenAICompatibleSemanticProvider(
            model=normalized.model,
            api_key=api_key,
            base_url=normalized.base_url,
            client=client,
            image_detail=normalized.image_detail,
            max_output_tokens=normalized.max_output_tokens,
            structured_mode=normalized.structured_mode,
            compat_profile=normalized.compat_profile,
            request_timeout_seconds=normalized.request_timeout_seconds,
            enable_thinking=normalized.enable_thinking,
        )

    if client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ProviderConfigurationError(
                "缺少 openai Python SDK。请先安装 requirements.txt。"
            ) from exc
        client = OpenAI(
            api_key=api_key,
            timeout=normalized.request_timeout_seconds,
            max_retries=0,
        )
    return OpenAISemanticProvider(
        model=normalized.model,
        client=client,
        image_detail=normalized.image_detail,
        max_output_tokens=normalized.max_output_tokens,
        request_timeout_seconds=normalized.request_timeout_seconds,
    )
