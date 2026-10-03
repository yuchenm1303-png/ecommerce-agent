from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QFrame, QGridLayout, QHBoxLayout, QWidget

from .ai_settings_surface import (
    AISettingsContent as _BaseAISettingsContent,
    AISettingsModalController as _BaseAISettingsModalController,
)


class AISettingsContent(_BaseAISettingsContent):
    """Compact connection-first presentation over the verified AI settings core.

    The common path intentionally exposes only one connection:
      Base URL + API Key -> discover /models -> choose role models -> capability probe.

    Manual model IDs are fallback-only when a provider does not expose /models.
    Fact/Web connection overrides remain available behind one explicit advanced toggle.
    The underlying persistence, capability probes and runtime admission gates are inherited
    unchanged from ``ai_settings_surface``.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._compact_role_manual_labels: dict[str, QLabel] = {}
        self._compact_advanced: QFrame | None = None
        self._compact_advanced_button: QPushButton | None = None
        self._apply_compact_presentation()

    @staticmethod
    def _grid_label(layout: QGridLayout | None, row: int) -> QLabel | None:
        if layout is None:
            return None
        item = layout.itemAtPosition(row, 0)
        widget = item.widget() if item is not None else None
        return widget if isinstance(widget, QLabel) else None

    def _apply_compact_presentation(self) -> None:
        root_layout = self.layout()
        if root_layout is None:
            return

        # Make the workflow explicit instead of exposing implementation details first.
        for label in self.findChildren(QLabel):
            text = label.text()
            if text.startswith("先配置连接，再读取该 Key"):
                label.setText(
                    "正常接入只需要 4 步：① 填 Base URL + API Key  "
                    "② 探测该 Key 可用模型  ③ 为三个角色选择模型  ④ 一键能力测试并保存。"
                )
            elif text.startswith("角色绑定。主语义始终使用默认连接"):
                label.setText(
                    "从刚才探测到的模型中选择：主语义 / Fact / Web。"
                    "通常三者都直接复用上面的同一条中转站连接。"
                )

        self.main_catalog_button.setText("探测连接并读取可用模型")
        self.verify_button.setText("测试所选模型能力")

        # The default connection card does not need to advertise the transport type.
        default_card = self.base_url.parentWidget()
        default_layout = default_card.layout() if default_card is not None else None
        if isinstance(default_layout, QGridLayout):
            label = self._grid_label(default_layout, 0)
            value_item = default_layout.itemAtPosition(0, 1)
            value = value_item.widget() if value_item is not None else None
            if label is not None:
                label.hide()
            if value is not None:
                value.hide()

        # Manual IDs are fallback-only. Hide all three duplicate rows on the normal path.
        role_card = self.model.parentWidget()
        role_layout = role_card.layout() if role_card is not None else None
        if isinstance(role_layout, QGridLayout):
            for role, row in (("semantic", 1), ("fact", 3), ("web", 5)):
                label = self._grid_label(role_layout, row)
                if label is not None:
                    self._compact_role_manual_labels[role] = label
                    label.hide()
                self._role_manual(role).hide()

        # Fact/Web independent credentials are an escape hatch, not the main workflow.
        advanced = self.fact_override.parentWidget()
        if isinstance(advanced, QFrame):
            self._compact_advanced = advanced
            button = QPushButton("高级连接覆盖")
            button.setObjectName("quietButton")
            button.setCheckable(True)
            button.setToolTip("仅当 Fact 或 Web 必须使用另一家服务 / 另一把 Key 时才需要")
            button.toggled.connect(self._set_advanced_visible)
            self._compact_advanced_button = button

            index = root_layout.indexOf(advanced)
            root_layout.insertWidget(max(0, index), button, 0, Qt.AlignmentFlag.AlignLeft)
            enabled = bool(self.fact_override.isChecked() or self.web_override.isChecked())
            button.setChecked(enabled)
            self._set_advanced_visible(enabled)

        self._sync_manual_fallback_visibility()

    def _set_advanced_visible(self, visible: bool) -> None:
        if self._compact_advanced is not None:
            self._compact_advanced.setVisible(bool(visible))
        if self._compact_advanced_button is not None:
            self._compact_advanced_button.setText(
                "收起高级连接覆盖" if visible else "高级连接覆盖"
            )

    def _sync_manual_fallback_visibility(self) -> None:
        for role in ("semantic", "fact", "web"):
            connection = self._connection_for_role(role)
            # Manual input is only justified when /models is explicitly unavailable.
            visible = self._catalog_available.get(connection) is False
            manual = self._role_manual(role)
            manual.setVisible(visible)
            label = self._compact_role_manual_labels.get(role)
            if label is not None:
                label.setVisible(visible)

    def _apply_catalog_to_role(self, role: str) -> None:
        super()._apply_catalog_to_role(role)
        # During base __init__ these compact fields do not exist yet.
        if hasattr(self, "_compact_role_manual_labels"):
            self._sync_manual_fallback_visibility()


class AISettingsModalController(_BaseAISettingsModalController):
    """Use the compact surface while preserving the existing runtime binding logic."""

    def open(self) -> None:
        details = getattr(self.window, "_card_details", None)
        open_custom = getattr(details, "open_custom", None)
        body_layout = getattr(details, "body_layout", None)
        body = getattr(details, "body", None)
        if not callable(open_custom) or body_layout is None or not isinstance(body, QWidget):
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self.window, "设置无法打开", "详情弹窗组件尚未初始化。")
            return

        def populate() -> None:
            panel = AISettingsContent(body)
            self._panel = panel
            body_layout.addWidget(panel, 1)

        open_custom(
            title="AI 服务设置",
            eyebrow="SETTINGS · AI CONNECTION",
            populate=populate,
            ratio=(0.72, 0.84),
        )


def install_ai_settings_modal(window) -> AISettingsModalController:
    existing = getattr(window, "_ai_settings_controller", None)
    if isinstance(existing, AISettingsModalController):
        return existing
    controller = AISettingsModalController(window)
    window._ai_settings_controller = controller
    return controller


__all__ = ["AISettingsContent", "AISettingsModalController", "install_ai_settings_modal"]
