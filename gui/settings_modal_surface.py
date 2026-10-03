"""Backward-compatible import surface for the AI connection settings panel.

The active implementation keeps the proven official-Qwen profile intact while
allowing the relay side to use a small connection pool. Each AI role can bind to
its own relay URL / key / model and is capability-tested before runtime use.
"""

from .ai_settings_pool_surface import (
    AISettingsContent,
    AISettingsModalController,
    install_ai_settings_modal,
)

__all__ = ["AISettingsContent", "AISettingsModalController", "install_ai_settings_modal"]
