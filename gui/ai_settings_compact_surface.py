from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QFrame, QGridLayout, QWidget

from app.ai_connection_probe import CapabilityProbeError
from .ai_settings_surface import (
    AISettingsContent as _BaseAISettingsContent,
    AISettingsModalController as _BaseAISettingsModalController,
)


class AISettingsContent(_BaseAISettingsContent):
    """Compact connection-first presentation over the verified AI settings core.

    Normal setup is deliberately one path:
      Base URL + API Key -> discover /models -> choose role models -> capability probe.

    Manual model IDs appear only when a provider does not expose /models. Fact/Web
    connection overrides remain available behind one explicit advanced toggle. The
    persistence, capability probes and runtime admission gates stay in the verified
    base implementation and are not duplicated here.
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

        # Hide transport implementation detail on the common path.
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

        # Manual model IDs are fallback-only and stay invisible when /models works.
        role_card = self.model.parentWidget()
        role_layout = role_card.layout() if role_card is not None else None
        if isinstance(role_layout, QGridLayout):
            for role, row in (("semantic", 1), ("fact", 3), ("web", 5)):
                label = self._grid_label(role_layout, row)
                if label is not None:
                    self._compact_role_manual_labels[role] = label
                    label.hide()
                self._role_manual(role).hide()

        # Fact/Web independent credentials are an escape hatch, not the main UI.
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

        # The feature is locked in this build; do not occupy the connection workflow.
        optimization_card = self.image_optimization.parentWidget()
        if isinstance(optimization_card, QFrame):
            optimization_card.hide()

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
            visible = self._catalog_available.get(connection) is False
            manual = self._role_manual(role)
            manual.setVisible(visible)
            label = self._compact_role_manual_labels.get(role)
            if label is not None:
                label.setVisible(visible)

    def _apply_catalog_to_role(self, role: str) -> None:
        super()._apply_catalog_to_role(role)
        if hasattr(self, "_compact_role_manual_labels"):
            self._sync_manual_fallback_visibility()

    def _start_capability_probe(self) -> None:
        # New/edited managed configurations must discover each active connection first.
        # A provider that explicitly rejects /models is still supported through the
        # manual-ID fallback because its catalog state becomes False rather than None.
        active_connections = {"main"}
        if self.fact_override.isChecked():
            active_connections.add("fact")
        if self.web_override.isChecked():
            active_connections.add("web")
        pending = [name for name in active_connections if self._catalog_available.get(name) is None]
        if pending:
            from PySide6.QtWidgets import QMessageBox

            names = {"main": "默认连接", "fact": "Fact 独立连接", "web": "Web 独立连接"}
            QMessageBox.warning(
                self,
                "请先探测可用模型",
                "能力测试前必须先读取当前连接的模型目录："
                + "、".join(names[name] for name in sorted(pending))
                + "。如果服务不支持 /models，探测会自动切换到手动模型 ID 回退。",
            )
            return
        try:
            super()._start_capability_probe()
        except CapabilityProbeError as exc:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "无法开始能力测试", str(exc))


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
