from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .openai_compatible import OpenAICompatibleSemanticProvider
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


@dataclass(slots=True, frozen=True)
class ProviderConfig:
    provider: str
    model: str
    api_key_env: str
    base_url: str = ""
    image_detail: str = "auto"
    max_output_tokens: int = 12000
    structured_mode: str = "prompt_only"

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
    if not config.model.strip():
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
    return ProviderConfig(
        provider=provider,
        model=config.model.strip(),
        api_key_env=config.api_key_env.strip(),
        base_url=base_url,
        image_detail=config.image_detail,
        max_output_tokens=int(config.max_output_tokens),
        structured_mode=config.structured_mode,
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
        )

    # Native OpenAI keeps the existing Responses API adapter and strict JSON
    # schema behavior. When a custom env variable is requested we construct the
    # SDK client explicitly rather than mutating process-global environment.
    if client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ProviderConfigurationError(
                "缺少 openai Python SDK。请先安装 requirements.txt。"
            ) from exc
        client = OpenAI(api_key=api_key)
    return OpenAISemanticProvider(
        model=normalized.model,
        client=client,
        image_detail=normalized.image_detail,
        max_output_tokens=normalized.max_output_tokens,
    )
