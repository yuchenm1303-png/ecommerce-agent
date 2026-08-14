from __future__ import annotations

import json
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
SUPPORTED_STRUCTURED_MODES = ("auto", "prompt_only", "json_object")
_VERTICAL_PLAN_TASK = "plan_makro_vertical_search_intents"
_VERTICAL_CHOICE_TASK = "choose_exact_makro_vertical_from_aggregated_live_search"


def _vertical_diag(event: str, payload: dict[str, Any]) -> None:
    """Emit one compact machine-readable Step 1 diagnostic line.

    Only Makro Vertical semantic tasks use this channel. Provider credentials and
    transport configuration are never included, so GUI logs can be shared for
    category diagnosis without exposing API keys.
    """

    body = {"event": str(event or "unknown"), **payload}
    print(
        "MAKRO_VERTICAL_DIAG "
        + json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str),
        flush=True,
    )


class _VerticalDiagnosticProvider:
    """Transparent provider proxy that traces only Makro Vertical AI decisions."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    @property
    def name(self) -> str:
        return str(getattr(self._delegate, "name", "semantic-provider"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        task = str(request_payload.get("task") or "").strip()
        context = request_payload.get("context") or {}

        if task == _VERTICAL_PLAN_TASK:
            _vertical_diag(
                "identity",
                {
                    "product_type_en": context.get("product_type_en", ""),
                    "product_summary": context.get("product_summary", ""),
                    "product_identity": context.get("product_identity") or {},
                },
            )
        elif task == _VERTICAL_CHOICE_TASK:
            ladder = context.get("search_queries_specific_to_broad") or context.get("search_queries") or []
            candidates = context.get("live_candidates") or []
            _vertical_diag(
                "candidate_pool",
                {
                    "search_ladder": ladder,
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                },
            )

        try:
            result = self._delegate.extract_json(request_payload)
        except Exception as exc:
            if task in {_VERTICAL_PLAN_TASK, _VERTICAL_CHOICE_TASK}:
                _vertical_diag(
                    "provider_error",
                    {"task": task, "error": f"{type(exc).__name__}: {exc}"},
                )
            raise

        if task == _VERTICAL_PLAN_TASK:
            _vertical_diag(
                "search_plan",
                {
                    "specific_queries": result.get("specific_queries") or result.get("queries") or [],
                    "broader_queries": result.get("broader_queries") or [],
                    "head_noun_query": result.get("head_noun_query") or "",
                },
            )
        elif task == _VERTICAL_CHOICE_TASK:
            selected = str(result.get("selected_vertical") or "").strip()
            candidates = context.get("live_candidates") or []
            evidence: dict[str, Any] = {}
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                if str(candidate.get("label") or "").strip().casefold() == selected.casefold():
                    evidence = dict(candidate)
                    break
            _vertical_diag(
                "decision",
                {
                    "selected_vertical": selected,
                    "selection_relation": result.get("selection_relation") or "",
                    "selected_candidate_evidence": evidence,
                    "replay_queries": evidence.get("matched_queries") or [],
                },
            )

        return result


def _with_vertical_diagnostics(provider: Any) -> Any:
    if isinstance(provider, _VerticalDiagnosticProvider):
        return provider
    return _VerticalDiagnosticProvider(provider)


def _validated_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderConfigurationError("--base-url 必须是完整 http/https API 根地址。")
    return url


def _safe_base_url(value: str) -> str:
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


def _effective_structured_mode(
    provider: str,
    compat_profile: str,
    requested: str,
) -> str:
    if requested != "auto":
        return requested
    if provider == "openai-compatible" and compat_profile == "qwen-omni":
        return "json_object"
    if provider == "openai-compatible":
        return "prompt_only"
    return "auto"


def _effective_thinking(
    provider: str,
    compat_profile: str,
    structured_mode: str,
    requested: bool | None,
) -> bool | None:
    if provider != "openai-compatible":
        if requested is not None:
            raise ProviderConfigurationError(
                "thinking 开关当前仅用于 openai-compatible provider。"
            )
        return None

    if structured_mode == "json_object" and requested is True:
        raise ProviderConfigurationError(
            "JSON mode 为保证结构化输出稳定，必须关闭 thinking；请使用 --disable-thinking "
            "或改用 --structured-mode prompt_only。"
        )
    if requested is not None:
        return bool(requested)
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
    structured_mode: str = "auto"
    compat_profile: str = "generic"
    request_timeout_seconds: float = 120.0
    enable_thinking: bool | None = None

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "base_url": _safe_base_url(self.base_url),
            "image_detail": self.image_detail,
            # JSON mode intentionally ignores max_output_tokens so output cannot
            # be truncated mid-object.
            "max_output_tokens": (
                None if self.structured_mode == "json_object" else self.max_output_tokens
            ),
            "structured_mode": self.structured_mode,
            "compat_profile": self.compat_profile,
            "request_timeout_seconds": self.request_timeout_seconds,
            "enable_thinking": self.enable_thinking,
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
    if config.structured_mode not in SUPPORTED_STRUCTURED_MODES:
        raise ProviderConfigurationError(
            "--structured-mode 必须是 " + "/".join(SUPPORTED_STRUCTURED_MODES) + "。"
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
            "--request-timeout-seconds 必须在 10..600 秒。"
        )

    effective_profile = _effective_compat_profile(provider, model, config.compat_profile)
    effective_structured_mode = _effective_structured_mode(
        provider, effective_profile, config.structured_mode
    )
    effective_thinking = _effective_thinking(
        provider,
        effective_profile,
        effective_structured_mode,
        config.enable_thinking,
    )
    return ProviderConfig(
        provider=provider,
        model=model,
        api_key_env=config.api_key_env.strip(),
        base_url=base_url,
        image_detail=config.image_detail,
        max_output_tokens=int(config.max_output_tokens),
        structured_mode=effective_structured_mode,
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
        provider = OpenAICompatibleSemanticProvider(
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
        return _with_vertical_diagnostics(provider)

    if client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise ProviderConfigurationError(
                "缺少 openai Python SDK。请先安装 requirements.txt。"
            ) from exc
        client = OpenAI(
            api_key=api_key,
            timeout=normalized.request_timeout_seconds,
            max_retries=0,
        )
    provider = OpenAISemanticProvider(
        model=normalized.model,
        client=client,
        image_detail=normalized.image_detail,
        max_output_tokens=normalized.max_output_tokens,
        request_timeout_seconds=normalized.request_timeout_seconds,
    )
    return _with_vertical_diagnostics(provider)
