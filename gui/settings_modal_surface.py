"""Backward-compatible import surface for the AI connection settings panel.

The implementation lives in :mod:`gui.ai_settings_surface` so the connection,
model-discovery and capability-admission workflow stays isolated from the rest of
the settings shell. Existing imports intentionally keep working unchanged.
"""

from .ai_settings_surface import (
    AISettingsContent,
    AISettingsModalController,
    install_ai_settings_modal,
)

__all__ = ["AISettingsContent", "AISettingsModalController", "install_ai_settings_modal"]
