from __future__ import annotations

"""Compatibility exports for the canonical shared-detail AI settings surface."""

from .settings_modal_surface import (
    AISettingsContent,
    AISettingsModalController,
    install_ai_settings_modal,
)

AISettingsDialog = AISettingsContent
AISettingsController = AISettingsModalController


def install_ai_settings(window):
    return install_ai_settings_modal(window)


__all__ = [
    "AISettingsDialog",
    "AISettingsController",
    "AISettingsContent",
    "AISettingsModalController",
    "install_ai_settings",
    "install_ai_settings_modal",
]
