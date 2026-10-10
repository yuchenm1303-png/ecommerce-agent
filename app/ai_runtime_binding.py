"""Resolve the selected Qwen/relay route once and keep child processes isolated.

No API keys are written to job manifests, command lines or diagnostic output.
A batch account lane owns its in-memory route snapshot throughout the run.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .ai_connection_probe import (
    CapabilityProbeError,
    RoleBinding,
    assert_verified_if_managed,
)
from .ai_profile_store import (
    AI_SOURCE_QWEN,
    AI_SOURCE_RELAY,
    load_active_ai_source,
    load_qwen_profile,
    relay_verification_directory,
)
from .ai_relay_pool import load_relay_connection_key, load_relay_pool
from .ai_service_settings import (
    RUNTIME_FACT_BASE_URL_ENV,
    RUNTIME_FACT_KEY_ENV,
    RUNTIME_WEB_BASE_URL_ENV,
    RUNTIME_WEB_KEY_ENV,
)

_RUNTIME_KEY_ENV = "AI_API_KEY"
_AI_CHILD_ENV_NAMES = (
    _RUNTIME_KEY_ENV,
    RUNTIME_FACT_BASE_URL_ENV,
    RUNTIME_FACT_KEY_ENV,
    RUNTIME_WEB_BASE_URL_ENV,
    RUNTIME_WEB_KEY_ENV,
)
# These secrets are never needed by child workflows once the selected profile
# has been resolved. Do not leak unused provider keys across account lanes.
_UNUSED_PROVIDER_KEYS = (
    "OPENAI_API_KEY",
    "ECOMMERCE_AGENT_AI_API_KEY",
    "ECOMMERCE_AGENT_AI_RELAY_API_KEY",
    "ECOMMERCE_AGENT_AI_RELAY_2_API_KEY",
    "ECOMMERCE_AGENT_AI_RELAY_3_API_KEY",
)


def apply_active_ai_runtime(config: Any) -> Any:
    """Select exactly one saved route, verify it, and freeze its credentials.

    Keep Qwen and relay credentials independent. Never fall back to Qwen if a
    selected relay is missing, unverified or invalid.
    """
    source = load_active_ai_source()
    runtime: dict[str, str] = {}
    if source == AI_SOURCE_QWEN:
        settings, key = load_qwen_profile()
        base_url = settings.base_url
        semantic_model = settings.model
        fact_model = settings.fact_model
        web_model = settings.web_model
        runtime[_RUNTIME_KEY_ENV] = key
    elif source == AI_SOURCE_RELAY:
        pool = load_relay_pool()
        if pool is None:
            raise CapabilityProbeError("已选择中转站，但中转连接池尚未配置。")
        bindings: dict[str, RoleBinding] = {}
        for role in ("semantic", "fact", "web"):
            choice = pool.binding_for(role)
            connection = pool.connection(choice.connection_id)
            key = load_relay_connection_key(choice.connection_id)
            if not key:
                raise CapabilityProbeError(f"中转站 {choice.connection_id} 缺少 API Key。")
            bindings[role] = RoleBinding(
                role, connection.base_url, choice.model, key
            ).normalized()
        ordered = tuple(bindings[role] for role in ("semantic", "fact", "web"))
        if not assert_verified_if_managed(
            config_dir=relay_verification_directory(), bindings=ordered
        ):
            raise CapabilityProbeError("中转站连接池缺少能力验证记录，请先完成模型能力测试。")
        semantic, fact, web = ordered
        base_url = semantic.base_url
        semantic_model = semantic.model
        fact_model = fact.model
        web_model = web.model
        runtime[_RUNTIME_KEY_ENV] = semantic.api_key
        if (fact.base_url, fact.api_key) != (semantic.base_url, semantic.api_key):
            runtime[RUNTIME_FACT_BASE_URL_ENV] = fact.base_url
            runtime[RUNTIME_FACT_KEY_ENV] = fact.api_key
        # Explicit web binding keeps the compatible Responses path authorized,
        # even when Web shares the semantic endpoint.
        runtime[RUNTIME_WEB_BASE_URL_ENV] = web.base_url
        runtime[RUNTIME_WEB_KEY_ENV] = web.api_key
    else:
        raise CapabilityProbeError(f"未知 AI 来源：{source}")

    config.provider = "openai-compatible"
    config.base_url = base_url
    config.local_model = semantic_model
    config.fact_model = fact_model
    config.web_model = web_model
    config.api_key_env = _RUNTIME_KEY_ENV
    config.ai_source = source
    config.runtime_ai_env = dict(runtime)

    # Preserve compatibility with existing in-process resolver initialization;
    # subprocesses must use apply_child_ai_environment rather than reading
    # these mutable globals.
    for name in _AI_CHILD_ENV_NAMES:
        os.environ.pop(name, None)
    os.environ.update(runtime)
    return config


def freeze_ai_runtime_environment(config: Any) -> None:
    """Snapshot CLI/dev environments once; never replace an existing snapshot."""
    if getattr(config, "runtime_ai_env", None):
        return
    key_env = str(getattr(config, "api_key_env", "") or "").strip()
    if not key_env or not os.getenv(key_env, "").strip():
        raise ValueError(f"当前任务的 AI API Key ({key_env or '未指定'}) 未配置。")
    names = set(_AI_CHILD_ENV_NAMES) - {_RUNTIME_KEY_ENV}
    names.add(key_env)
    config.runtime_ai_env = {
        name: value
        for name in names
        if (value := os.getenv(name, "").strip())
    }


def ai_route_fingerprint(config: Any) -> str:
    """Hash route identity without writing a Base URL or secret to batch.json.

    Credential rotation on the same provider/role route is allowed; switching
    models/endpoints or Qwen versus relay requires a fresh batch.
    """
    env = getattr(config, "runtime_ai_env", {}) or {}
    payload = {
        "source": str(getattr(config, "ai_source", "") or "manual"),
        "provider": str(getattr(config, "provider", "") or ""),
        "semantic_url": str(getattr(config, "base_url", "") or ""),
        "semantic_model": str(getattr(config, "local_model", "") or ""),
        "fact_model": str(getattr(config, "fact_model", "") or ""),
        "web_model": str(getattr(config, "web_model", "") or ""),
        "fact_url": str(env.get(RUNTIME_FACT_BASE_URL_ENV, "") or ""),
        "web_url": str(env.get(RUNTIME_WEB_BASE_URL_ENV, "") or ""),
    }
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def apply_child_ai_environment(environment: Any, config: Any) -> None:
    """Remove inherited cross-lane credentials and inject this run's snapshot."""
    freeze_ai_runtime_environment(config)
    for name in set(_AI_CHILD_ENV_NAMES) | set(_UNUSED_PROVIDER_KEYS) | {str(config.api_key_env)}:
        environment.remove(name)
    for name, value in config.runtime_ai_env.items():
        environment.insert(name, value)


__all__ = [
    "apply_active_ai_runtime",
    "apply_child_ai_environment",
    "ai_route_fingerprint",
    "freeze_ai_runtime_environment",
]
