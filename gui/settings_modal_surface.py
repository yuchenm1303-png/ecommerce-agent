"""Backward-compatible import surface for the AI connection settings panel.

The implementation keeps the verified connection/probe core in
:mod:`gui.ai_settings_surface`, while :mod:`gui.ai_settings_compact_surface`
presents the normal setup as one simple flow: connection -> model discovery ->
role selection -> capability validation. Existing imports intentionally keep
working unchanged.
"""

from .ai_settings_compact_surface import (
    AISettingsContent,
    AISettingsModalController,
    install_ai_settings_modal,
)

__all__ = ["AISettingsContent", "AISettingsModalController", "install_ai_settings_modal"]
