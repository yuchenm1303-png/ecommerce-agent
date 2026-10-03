"""Backward-compatible import surface for the AI connection settings panel.

The active implementation keeps the proven official-Qwen profile intact while
allowing the relay side to use a small connection pool. Each AI role can bind to
its own relay URL / key / model and is capability-tested before runtime use.

Relay Web bindings are always published explicitly to the Resolver, even when Web
shares the same relay connection as the semantic role. This avoids the legacy
DashScope-domain safety gate accidentally disabling a verified relay Web model.
"""

import os

from app.ai_profile_store import AI_SOURCE_RELAY, load_active_ai_source
from app.ai_relay_pool import load_relay_connection_key, load_relay_pool
from app.ai_service_settings import RUNTIME_WEB_BASE_URL_ENV, RUNTIME_WEB_KEY_ENV
from .ai_settings_pool_surface import (
    AISettingsContent,
    AISettingsModalController as _BaseAISettingsModalController,
)


class AISettingsModalController(_BaseAISettingsModalController):
    def _apply_runtime(self, config) -> None:
        super()._apply_runtime(config)
        if load_active_ai_source() != AI_SOURCE_RELAY:
            return
        pool = load_relay_pool()
        if pool is None:
            return
        choice = pool.binding_for("web")
        connection = pool.connection(choice.connection_id)
        key = load_relay_connection_key(choice.connection_id)
        if not key:
            return
        # Explicitly mark the verified relay Web route. Resolver treats this as an
        # opt-in compatible Responses endpoint and lets the provider negotiate the
        # actual protocol (OpenAI standard first, DashScope fallback).
        os.environ[RUNTIME_WEB_BASE_URL_ENV] = connection.base_url
        os.environ[RUNTIME_WEB_KEY_ENV] = key


def install_ai_settings_modal(window) -> AISettingsModalController:
    existing = getattr(window, "_ai_settings_controller", None)
    if isinstance(existing, AISettingsModalController):
        return existing
    controller = AISettingsModalController(window)
    window._ai_settings_controller = controller
    return controller


__all__ = ["AISettingsContent", "AISettingsModalController", "install_ai_settings_modal"]
