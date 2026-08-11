"""Header switch controlling the optional Runtime Assistant OS window."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget

from .mode_toggle import WorkspaceModeSwitch


class RuntimeAssistantSwitch(WorkspaceModeSwitch):
    """Reuse the established Single/Batch micro-switch visuals for the float window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("runtimeAssistantSwitch")
        self.setAccessibleName("Runtime Assistant floating window")
        self.set_checked_immediate(False)

    def _sync_tooltip(self, checked: bool) -> None:
        if checked:
            self.setToolTip("Runtime Assistant 浮窗已开启 · 点击关闭")
        else:
            self.setToolTip("Runtime Assistant 浮窗已关闭 · 点击开启")


def install_runtime_assistant_toggle(
    window: QMainWindow,
    assistant: Any,
) -> RuntimeAssistantSwitch:
    """Append a default-off float-window switch beside the existing mode switch."""

    existing = getattr(window, "_runtime_assistant_switch", None)
    if isinstance(existing, RuntimeAssistantSwitch):
        return existing

    root = window.centralWidget()
    outer = root.layout() if root is not None else None
    if root is None or not isinstance(outer, QVBoxLayout) or outer.count() < 1:
        raise RuntimeError("Runtime Assistant switch expected the preserved root layout")

    header_item = outer.itemAt(0)
    header = header_item.layout() if header_item is not None else None
    if not isinstance(header, QHBoxLayout):
        raise RuntimeError("Runtime Assistant switch expected the common header row")

    label = QLabel("浮窗", root)
    label.setObjectName("runtimeAssistantToggleLabel")
    label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
    label.setFixedHeight(32)
    label.setStyleSheet("color: rgba(232, 241, 252, 178);")

    toggle = RuntimeAssistantSwitch(root)
    toggle.set_checked_immediate(False)

    setter = getattr(assistant, "set_user_visible", None)
    if not callable(setter):
        raise RuntimeError("Runtime Assistant switch requires set_user_visible()")

    # Explicitly establish the requested startup state every launch. This is not
    # persisted: the floating window is intentionally opt-in for each GUI session.
    setter(False)
    toggle.toggled.connect(setter)

    header.addSpacing(10)
    header.addWidget(label, 0, Qt.AlignmentFlag.AlignBottom)
    header.addWidget(toggle, 0, Qt.AlignmentFlag.AlignBottom)

    window._runtime_assistant_toggle_label = label  # type: ignore[attr-defined]
    window._runtime_assistant_switch = toggle  # type: ignore[attr-defined]
    return toggle


__all__ = [
    "RuntimeAssistantSwitch",
    "install_runtime_assistant_toggle",
]
