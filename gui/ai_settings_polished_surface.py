from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .ai_settings_pool_surface import (
    AISettingsContent as _PoolAISettingsContent,
    AISettingsModalController as _PoolAISettingsModalController,
)


_POLISH_STYLE = r"""
QWidget#aiPoolSettings { background: transparent; }
QWidget#aiPoolSettings QLabel#aiHint {
    color: rgba(236,242,250,172);
    font-size: 10px;
}
QWidget#aiPoolSettings QLabel#aiTitle {
    color: rgba(250,252,255,244);
    font-size: 12px;
    font-weight: 760;
}
QWidget#aiPoolSettings QLabel#aiTableHeader {
    color: rgba(229,236,247,142);
    font-size: 9px;
    font-weight: 680;
}
QWidget#aiPoolSettings QLabel#aiMeta {
    color: rgba(230,237,247,140);
    font-size: 9px;
}
QWidget#aiPoolSettings QLabel#aiPass {
    color: #9fe2bd;
    font-size: 9px;
    font-weight: 680;
}
QWidget#aiPoolSettings QLabel#aiWarn {
    color: #f0c977;
    font-size: 9px;
    font-weight: 680;
}
QWidget#aiPoolSettings QLabel#aiFail {
    color: #f0a1ad;
    font-size: 9px;
    font-weight: 680;
}
QWidget#aiPoolSettings QFrame#cardDetailSection {
    background-color: rgba(22,34,50,52);
    border: 1px solid rgba(255,255,255,16);
    border-radius: 11px;
}
QWidget#aiPoolSettings QLineEdit#aiInput,
QWidget#aiPoolSettings QComboBox#modalCombo {
    min-height: 34px;
    max-height: 36px;
    padding: 0 10px;
    color: rgba(250,252,255,236);
    background-color: rgba(12,22,36,92);
    border: 1px solid rgba(255,255,255,22);
    border-radius: 8px;
}
QWidget#aiPoolSettings QLineEdit#aiInput:focus,
QWidget#aiPoolSettings QComboBox#modalCombo:focus {
    border: 1px solid rgba(177,169,255,110);
    background-color: rgba(15,25,42,112);
}
QWidget#aiPoolSettings QLineEdit#aiInput:read-only {
    color: rgba(240,244,250,176);
    background-color: rgba(12,22,36,54);
}
QWidget#aiPoolSettings QPushButton#modalPrimaryButton {
    min-height: 32px;
    max-height: 34px;
    padding: 0 13px;
}
"""


class AISettingsContent(_PoolAISettingsContent):
    """Visual refinement layer for the proven AI settings behavior.

    The underlying pool/profile/probe implementation remains untouched.  This layer
    only tightens geometry, prevents low-content profiles from vertically stretching,
    and improves the visual hierarchy presented by the Quick-modal mirror.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(self.styleSheet() + _POLISH_STYLE)
        self._polish_copy()
        self._polish_geometry()
        QTimer.singleShot(0, self._settle_layout)

    @staticmethod
    def _compact_layout(widget: QWidget, *, margins: tuple[int, int, int, int], spacing: int) -> None:
        layout = widget.layout()
        if layout is None:
            return
        layout.setContentsMargins(*margins)
        layout.setSpacing(spacing)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def _polish_copy(self) -> None:
        for label in self.findChildren(QLabel):
            text = label.text().strip()
            if text.startswith("官方 Qwen 与中转站完全隔离"):
                label.setText("Qwen 与中转站分开保存，切换来源不会覆盖另一套配置。")
                label.setMaximumHeight(34)
            elif text == "中转站 · 连接池":
                label.setText("中转站连接")
            elif text.startswith("先配置并探测连接"):
                label.setText("配置连接后先探测模型；主语义 / Fact / Web 可以复用同一连接，也可以分别绑定 A / B / C。")
            elif text in {"角色", "连接", "模型"}:
                label.setObjectName("aiTableHeader")

        self.catalog_button.setText("探测当前连接")
        self.verify_button.setText("验证三个角色能力")
        self.catalog_button.setMaximumWidth(190)
        self.verify_button.setMaximumWidth(210)
        self.save_button.setMaximumWidth(190)
        self.connection_key_action.setMaximumWidth(64)
        self.qwen_key_action.setMaximumWidth(64)

    def _polish_geometry(self) -> None:
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        root = self.layout()
        if isinstance(root, QVBoxLayout):
            root.setContentsMargins(0, 0, 0, 4)
            root.setSpacing(8)
            root.setAlignment(Qt.AlignmentFlag.AlignTop)

        # QFrame uses a grow-capable default size policy.  In a tall modal this made
        # the short Qwen profile absorb hundreds of pixels of empty height.  Every
        # section now hugs its natural size; the surrounding scroll area owns overflow.
        for frame in self.findChildren(QFrame):
            if frame.objectName() == "cardDetailSection":
                frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        for editor in self.findChildren(QLineEdit):
            if editor.objectName() == "aiInput":
                editor.setMinimumHeight(34)
                editor.setMaximumHeight(36)

        for combo in self.findChildren(QComboBox):
            if combo.objectName() == "modalCombo":
                combo.setMinimumHeight(34)
                combo.setMaximumHeight(36)

        for button in self.findChildren(QPushButton):
            if button.objectName() == "modalPrimaryButton":
                button.setMinimumHeight(32)
                button.setMaximumHeight(34)

        source_frame = self.source.parentWidget()
        if isinstance(source_frame, QFrame):
            source_frame.setMaximumHeight(60)
            self._compact_layout(source_frame, margins=(12, 9, 12, 9), spacing=6)

        self._compact_layout(self.qwen_card, margins=(14, 11, 14, 12), spacing=7)
        self._compact_layout(self.relay_card, margins=(14, 11, 14, 12), spacing=9)

        connection_frame = self.connection_slot.parentWidget()
        if isinstance(connection_frame, QFrame):
            self._compact_layout(connection_frame, margins=(11, 10, 11, 10), spacing=7)

        role_frame = self.role_connections["semantic"].parentWidget()
        if isinstance(role_frame, QFrame):
            self._compact_layout(role_frame, margins=(11, 9, 11, 10), spacing=6)

        capability_frame = self.capability_status["semantic"].parentWidget()
        if isinstance(capability_frame, QFrame):
            self._compact_layout(capability_frame, margins=(11, 9, 11, 10), spacing=6)

        for combo in self.role_connections.values():
            combo.setMinimumWidth(180)
            combo.setMaximumWidth(280)

    def _settle_layout(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()
            parent.update()

    def _source_changed(self, *_args: object) -> None:
        super()._source_changed(*_args)
        # Visibility switches change the natural height dramatically (Qwen is much
        # shorter than the relay pool).  Re-settle after Qt has processed visibility
        # so the Quick-modal mirror gets the compact geometry immediately.
        QTimer.singleShot(0, self._settle_layout)

    def _connection_slot_changed(self, *_args: object) -> None:
        super()._connection_slot_changed(*_args)
        cid = self._current_connection_id()
        if cid == "relay-1":
            self.connection_enabled.setText("默认连接 · 始终启用")
        else:
            self.connection_enabled.setText("启用此连接")
        QTimer.singleShot(0, self._settle_layout)


class AISettingsModalController(_PoolAISettingsModalController):
    """Use the polished visual surface while inheriting the proven runtime binding."""

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
            body_layout.addWidget(panel, 0, Qt.AlignmentFlag.AlignTop)

        open_custom(
            title="AI 服务设置",
            eyebrow="SETTINGS · AI PROFILES",
            populate=populate,
            ratio=(0.70, 0.82),
        )


def install_ai_settings_modal(window) -> AISettingsModalController:
    existing = getattr(window, "_ai_settings_controller", None)
    if isinstance(existing, AISettingsModalController):
        return existing
    controller = AISettingsModalController(window)
    window._ai_settings_controller = controller
    return controller


__all__ = ["AISettingsContent", "AISettingsModalController", "install_ai_settings_modal"]
