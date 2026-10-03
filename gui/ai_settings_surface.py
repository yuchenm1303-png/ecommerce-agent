from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ai_connection_probe import (
    CapabilityProbeError,
    ModelCatalogResult,
    RoleBinding,
    RoleCapabilityReport,
    assert_verified_if_managed,
    binding_signature,
    clear_verification_snapshot,
    discover_models,
    load_verification_snapshot,
    probe_role,
    save_verification_snapshot,
)
from app.ai_service_settings import (
    AIServiceSettings,
    RUNTIME_FACT_BASE_URL_ENV,
    RUNTIME_FACT_KEY_ENV,
    RUNTIME_WEB_BASE_URL_ENV,
    RUNTIME_WEB_KEY_ENV,
    clear_ai_service_key,
    has_ai_service_key,
    has_ai_service_role_key,
    load_ai_service_key,
    load_ai_service_role_key,
    load_ai_service_settings,
    resolved_ai_runtime,
    save_ai_service_role_key,
    save_ai_service_settings,
    settings_directory,
)
from app.image_optimization_gate import (
    image_optimization_enabled,
    set_image_optimization_enabled,
)


_RUNTIME_KEY_ENV = "AI_API_KEY"
_MASKED_KEY = "••••••••••••••••"
_IMAGE_OPTIMIZATION_GUI_UNLOCKED = False
_ROLE_LABELS = {"semantic": "主语义", "fact": "Fact", "web": "Web"}
_ROLE_REQUIREMENT_TEXT = {
    "semantic": "Chat Completions · Strict JSON Schema · Vision 实际读图",
    "fact": "Chat Completions · Strict JSON Schema",
    "web": "Responses API · web_search · 来源 URL",
}

_CONTENT_STYLE = r"""
QWidget#aiSettingsContent { background: transparent; }
QLabel#aiSettingsHint { color: rgba(255,255,255,196); font-size: 11px; }
QLabel#aiSettingsProvider { color: rgba(255,255,255,238); font-size: 12px; font-weight: 720; }
QLabel#aiRoleStatus { color: rgba(255,255,255,150); font-size: 10px; }
QLabel#aiCapabilityPass { color: #9fe2bd; font-size: 10px; font-weight: 680; }
QLabel#aiCapabilityWarn { color: #f4cb7a; font-size: 10px; font-weight: 680; }
QLabel#aiCapabilityFail { color: #f1a0ac; font-size: 10px; font-weight: 680; }
QLabel#imageOptimizationState { color: rgba(255,255,255,138); font-size: 11px; }
QCheckBox#imageOptimizationSwitch {
    color: rgba(255,255,255,226);
    spacing: 9px;
    font-size: 12px;
    font-weight: 700;
}
QCheckBox#imageOptimizationSwitch:disabled { color: rgba(255,255,255,116); }
QCheckBox#imageOptimizationSwitch::indicator {
    width: 38px;
    height: 20px;
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,34);
    background: rgba(255,255,255,32);
}
QCheckBox#imageOptimizationSwitch::indicator:checked {
    border-color: rgba(157,243,239,90);
    background: rgba(65,151,148,155);
}
QCheckBox#imageOptimizationSwitch::indicator:disabled {
    border-color: rgba(255,255,255,20);
    background: rgba(255,255,255,18);
}
QLineEdit#aiSettingsInput, QComboBox#modalCombo {
    min-height: 39px;
    padding: 0 12px;
    color: rgba(255,255,255,238);
    background-color: rgba(10,20,32,88);
    border: 1px solid rgba(255,255,255,24);
    border-radius: 8px;
    selection-background-color: rgba(137,190,226,118);
}
QLineEdit#aiSettingsInput:hover, QComboBox#modalCombo:hover {
    background-color: rgba(13,25,39,104);
    border-color: rgba(255,255,255,34);
}
QLineEdit#aiSettingsInput:focus, QComboBox#modalCombo:focus {
    background-color: rgba(11,23,37,122);
    border-color: rgba(176,220,249,92);
}
QLineEdit#aiSettingsInput:read-only {
    color: rgba(255,255,255,174);
    background-color: rgba(10,20,32,58);
    border-color: rgba(255,255,255,16);
}
QLineEdit#aiSettingsInput::placeholder { color: rgba(255,255,255,108); }
"""


@dataclass(slots=True)
class _ConnectionWidgets:
    base_url: QLineEdit
    key: QLineEdit
    key_action: QPushButton
    catalog_button: QPushButton
    catalog_status: QLabel


class AISettingsContent(QWidget):
    """Connection-first AI settings with preflight model capability admission."""

    catalog_finished = Signal(object)
    probe_finished = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiSettingsContent")
        self.setStyleSheet(_CONTENT_STYLE)

        self._suppress_dirty = False
        self._busy = False
        self._loaded_settings = AIServiceSettings()
        self._key_configured = False
        self._editing_key = False
        self._role_key_configured = {"fact": False, "web": False}
        self._role_key_editing = {"fact": False, "web": False}
        self._catalogs: dict[str, set[str] | None] = {"main": None, "fact": None, "web": None}
        self._catalog_available: dict[str, bool | None] = {"main": None, "fact": None, "web": None}
        self._validated_reports: dict[str, RoleCapabilityReport] = {}
        self._validated_signatures: dict[str, str] = {}
        self._persisted_snapshot_present = False

        self.catalog_finished.connect(self._catalog_done)
        self.probe_finished.connect(self._probe_done)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        intro = QLabel(
            "先配置连接，再读取该 Key 实际可访问的模型，最后验证所选模型是否真正满足上架角色能力。"
            "模型目录只用于发现，不能代替能力验证。"
        )
        intro.setObjectName("aiSettingsHint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Default connection -------------------------------------------------
        default_card = QFrame()
        default_card.setObjectName("cardDetailSection")
        default_form = QGridLayout(default_card)
        default_form.setContentsMargins(15, 14, 15, 15)
        default_form.setHorizontalSpacing(16)
        default_form.setVerticalSpacing(10)
        default_form.setColumnStretch(1, 1)

        provider = QLabel("OpenAI Compatible")
        provider.setObjectName("aiSettingsProvider")
        provider.setToolTip("当前生产 Resolver 使用 OpenAI-compatible 协议。")
        self.base_url = self._input("https://your-gateway.example/v1")
        self.api_key = self._input("输入默认连接 API Key")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_action = self._key_button(lambda: self._key_action_clicked("main"))
        key_row = self._key_row(self.api_key, self.key_action)

        self.main_catalog_button = QPushButton("测试连接并读取模型")
        self.main_catalog_button.setObjectName("modalPrimaryButton")
        self.main_catalog_button.clicked.connect(lambda: self._start_catalog("main"))
        self.main_catalog_status = self._status_label()

        default_rows: list[tuple[str, Any]] = [
            ("API 类型", provider),
            ("默认 Base URL", self.base_url),
            ("默认 API Key", key_row),
            ("", self.main_catalog_button),
            ("", self.main_catalog_status),
        ]
        self._add_rows(default_form, default_rows)
        layout.addWidget(default_card)

        # Role model selection ----------------------------------------------
        role_hint = QLabel(
            "角色绑定。主语义始终使用默认连接；Fact / Web 默认也复用默认连接和同一把 Key，"
            "只有确实需要另一个服务商或另一把 Key 时才开启独立连接。"
        )
        role_hint.setObjectName("aiSettingsHint")
        role_hint.setWordWrap(True)
        layout.addWidget(role_hint)

        role_card = QFrame()
        role_card.setObjectName("cardDetailSection")
        role_form = QGridLayout(role_card)
        role_form.setContentsMargins(15, 14, 15, 15)
        role_form.setHorizontalSpacing(16)
        role_form.setVerticalSpacing(10)
        role_form.setColumnStretch(1, 1)

        self.model = self._model_combo("主语义模型")
        self.fact_model = self._model_combo("Fact 模型")
        self.web_model = self._model_combo("Web 搜索模型")
        self.semantic_manual = self._manual_model_input("目录不可用时手动填写主语义模型 ID")
        self.fact_manual = self._manual_model_input("目录不可用时手动填写 Fact 模型 ID")
        self.web_manual = self._manual_model_input("目录不可用时手动填写 Web 模型 ID")

        role_rows: list[tuple[str, Any]] = [
            ("主语义模型", self.model),
            ("手动模型 ID", self.semantic_manual),
            ("事实模型", self.fact_model),
            ("手动模型 ID", self.fact_manual),
            ("Web 搜索模型", self.web_model),
            ("手动模型 ID", self.web_manual),
        ]
        self._add_rows(role_form, role_rows)
        layout.addWidget(role_card)

        # Optional role connections -----------------------------------------
        advanced = QFrame()
        advanced.setObjectName("cardDetailSection")
        advanced_form = QGridLayout(advanced)
        advanced_form.setContentsMargins(15, 14, 15, 15)
        advanced_form.setHorizontalSpacing(16)
        advanced_form.setVerticalSpacing(10)
        advanced_form.setColumnStretch(1, 1)

        self.fact_override = QCheckBox("Fact 使用独立连接")
        self.fact_override.setObjectName("imageOptimizationSwitch")
        self.fact_base_url = self._input("Fact 独立 Base URL")
        self.fact_key = self._input("Fact 独立 API Key")
        self.fact_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.fact_key_action = self._key_button(lambda: self._key_action_clicked("fact"))
        self.fact_catalog_button = QPushButton("读取 Fact 连接模型")
        self.fact_catalog_button.setObjectName("modalPrimaryButton")
        self.fact_catalog_button.clicked.connect(lambda: self._start_catalog("fact"))
        self.fact_catalog_status = self._status_label()

        self.web_override = QCheckBox("Web 使用独立连接")
        self.web_override.setObjectName("imageOptimizationSwitch")
        self.web_base_url = self._input("Web 独立 Base URL")
        self.web_key = self._input("Web 独立 API Key")
        self.web_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.web_key_action = self._key_button(lambda: self._key_action_clicked("web"))
        self.web_catalog_button = QPushButton("读取 Web 连接模型")
        self.web_catalog_button.setObjectName("modalPrimaryButton")
        self.web_catalog_button.clicked.connect(lambda: self._start_catalog("web"))
        self.web_catalog_status = self._status_label()

        advanced_rows: list[tuple[str, Any]] = [
            ("Fact", self.fact_override),
            ("Fact Base URL", self.fact_base_url),
            ("Fact API Key", self._key_row(self.fact_key, self.fact_key_action)),
            ("", self.fact_catalog_button),
            ("", self.fact_catalog_status),
            ("Web", self.web_override),
            ("Web Base URL", self.web_base_url),
            ("Web API Key", self._key_row(self.web_key, self.web_key_action)),
            ("", self.web_catalog_button),
            ("", self.web_catalog_status),
        ]
        self._add_rows(advanced_form, advanced_rows)
        layout.addWidget(advanced)

        # Capability admission ----------------------------------------------
        capability_card = QFrame()
        capability_card.setObjectName("cardDetailSection")
        capability_form = QGridLayout(capability_card)
        capability_form.setContentsMargins(15, 14, 15, 15)
        capability_form.setHorizontalSpacing(16)
        capability_form.setVerticalSpacing(9)
        capability_form.setColumnStretch(1, 1)

        self.capability_status: dict[str, QLabel] = {}
        row = 0
        for role in ("semantic", "fact", "web"):
            title = QLabel(_ROLE_LABELS[role])
            title.setObjectName("modalFieldLabel")
            status = self._status_label()
            status.setText("尚未验证 · " + _ROLE_REQUIREMENT_TEXT[role])
            self.capability_status[role] = status
            capability_form.addWidget(title, row, 0, Qt.AlignmentFlag.AlignTop)
            capability_form.addWidget(status, row, 1)
            row += 1

        self.verify_button = QPushButton("验证所选模型能力")
        self.verify_button.setObjectName("modalPrimaryButton")
        self.verify_button.clicked.connect(self._start_capability_probe)
        capability_form.addWidget(self.verify_button, row, 1)
        layout.addWidget(capability_card)

        capability_policy = QLabel(
            "能力验证是准入门槛：主语义必须真的读懂测试图片并满足严格 JSON Schema；Fact 必须满足严格 JSON Schema；"
            "Web 必须真的触发 Responses web_search 并返回来源 URL。验证后的 Base URL / Key / 模型任一变化都会使结果失效，"
            "重新验证前不会允许新的托管配置开始上架。"
        )
        capability_policy.setObjectName("cardDetailText")
        capability_policy.setWordWrap(True)
        layout.addWidget(capability_policy)

        # Existing optional image optimization ------------------------------
        optimization_card = QFrame()
        optimization_card.setObjectName("cardDetailSection")
        optimization_layout = QHBoxLayout(optimization_card)
        optimization_layout.setContentsMargins(15, 12, 15, 12)
        optimization_layout.setSpacing(10)
        self.image_optimization = QCheckBox("图像优化")
        self.image_optimization.setObjectName("imageOptimizationSwitch")
        self.image_optimization.setEnabled(_IMAGE_OPTIMIZATION_GUI_UNLOCKED)
        self.image_optimization.toggled.connect(self._image_optimization_toggled)
        self.image_optimization_state = QLabel("默认关闭 · 高负载 · 当前暂未开放")
        self.image_optimization_state.setObjectName("imageOptimizationState")
        optimization_layout.addWidget(self.image_optimization)
        optimization_layout.addWidget(self.image_optimization_state, 1)
        layout.addWidget(optimization_card)

        layout.addStretch(1)
        actions = QHBoxLayout()
        self.clear_key_button = QPushButton("清除默认 API Key")
        self.clear_key_button.setObjectName("modalDangerButton")
        self.clear_key_button.clicked.connect(self._clear_key)
        self.save_button = QPushButton("保存设置")
        self.save_button.setObjectName("modalPrimaryButton")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.clear_key_button)
        actions.addStretch(1)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)

        self._wire_dirty_signals()
        self.reload()

    # Widget helpers ---------------------------------------------------------
    @staticmethod
    def _input(placeholder: str) -> QLineEdit:
        editor = QLineEdit()
        editor.setObjectName("aiSettingsInput")
        editor.setPlaceholderText(placeholder)
        return editor

    @staticmethod
    def _model_combo(accessible_name: str) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName("modalCombo")
        combo.setAccessibleName(accessible_name)
        combo.setEditable(False)
        combo.setMinimumWidth(260)
        return combo

    @classmethod
    def _manual_model_input(cls, placeholder: str) -> QLineEdit:
        editor = cls._input(placeholder)
        editor.setEnabled(False)
        return editor

    @staticmethod
    def _status_label() -> QLabel:
        label = QLabel()
        label.setObjectName("aiRoleStatus")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _key_button(callback: Callable[[], None]) -> QPushButton:
        button = QPushButton("显示")
        button.setObjectName("modalPrimaryButton")
        button.setMaximumWidth(72)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _key_row(editor: QLineEdit, button: QPushButton) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        row_layout.addWidget(editor, 1)
        row_layout.addWidget(button)
        return row

    @staticmethod
    def _add_rows(layout: QGridLayout, rows: list[tuple[str, Any]]) -> None:
        for row, (text, widget) in enumerate(rows):
            label = QLabel(text)
            label.setObjectName("modalFieldLabel")
            layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(widget, row, 1)

    def _wire_dirty_signals(self) -> None:
        for editor in (self.base_url, self.fact_base_url, self.web_base_url):
            editor.textChanged.connect(self._configuration_changed)
        for editor in (self.semantic_manual, self.fact_manual, self.web_manual):
            editor.textChanged.connect(self._configuration_changed)
        for combo in (self.model, self.fact_model, self.web_model):
            combo.currentIndexChanged.connect(self._configuration_changed)
        self.fact_override.toggled.connect(self._override_changed)
        self.web_override.toggled.connect(self._override_changed)

    # Key state --------------------------------------------------------------
    def _set_main_key_state(self, configured: bool) -> None:
        self._key_configured = bool(configured)
        self._editing_key = not configured
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        if configured:
            self.api_key.setReadOnly(True)
            self.api_key.setText(_MASKED_KEY)
            self.key_action.setText("更改")
        else:
            self.api_key.setReadOnly(False)
            self.api_key.clear()
            self.api_key.setPlaceholderText("输入默认连接 API Key")
            self.key_action.setText("显示")

    def _set_role_key_state(self, role: str, configured: bool) -> None:
        editor = self.fact_key if role == "fact" else self.web_key
        action = self.fact_key_action if role == "fact" else self.web_key_action
        self._role_key_configured[role] = bool(configured)
        self._role_key_editing[role] = not configured
        editor.setEchoMode(QLineEdit.EchoMode.Password)
        if configured:
            editor.setReadOnly(True)
            editor.setText(_MASKED_KEY)
            action.setText("更改")
        else:
            editor.setReadOnly(False)
            editor.clear()
            editor.setPlaceholderText(f"{role.title()} 独立 API Key")
            action.setText("显示")

    def _key_action_clicked(self, connection: str) -> None:
        if connection == "main":
            configured = self._key_configured
            editing = self._editing_key
            editor = self.api_key
            action = self.key_action
            if configured and not editing:
                self._editing_key = True
                editor.setReadOnly(False)
                editor.clear()
                editor.setPlaceholderText("输入新的默认 API Key")
                action.setText("显示")
                self._invalidate_verification("默认 API Key 正在更改")
                editor.setFocus(Qt.FocusReason.OtherFocusReason)
                return
        else:
            configured = self._role_key_configured[connection]
            editing = self._role_key_editing[connection]
            editor = self.fact_key if connection == "fact" else self.web_key
            action = self.fact_key_action if connection == "fact" else self.web_key_action
            if configured and not editing:
                self._role_key_editing[connection] = True
                editor.setReadOnly(False)
                editor.clear()
                editor.setPlaceholderText(f"输入新的 {connection.title()} API Key")
                action.setText("显示")
                self._invalidate_verification(f"{connection.title()} API Key 正在更改")
                editor.setFocus(Qt.FocusReason.OtherFocusReason)
                return
        visible = editor.echoMode() == QLineEdit.EchoMode.Password
        editor.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        action.setText("隐藏" if visible else "显示")

    def _effective_key(self, connection: str) -> str:
        if connection == "main":
            if self._editing_key:
                value = self.api_key.text().strip()
                if value and value != _MASKED_KEY:
                    return value
            return load_ai_service_key(self._loaded_settings)
        editor = self.fact_key if connection == "fact" else self.web_key
        if self._role_key_editing[connection]:
            value = editor.text().strip()
            if value and value != _MASKED_KEY:
                return value
        return load_ai_service_role_key(connection)

    # Catalog/model handling -------------------------------------------------
    def _connection_base_url(self, connection: str) -> str:
        if connection == "main":
            return self.base_url.text().strip()
        return (self.fact_base_url if connection == "fact" else self.web_base_url).text().strip()

    def _connection_for_role(self, role: str) -> str:
        if role == "semantic":
            return "main"
        if role == "fact" and self.fact_override.isChecked():
            return "fact"
        if role == "web" and self.web_override.isChecked():
            return "web"
        return "main"

    def _role_combo(self, role: str) -> QComboBox:
        return {"semantic": self.model, "fact": self.fact_model, "web": self.web_model}[role]

    def _role_manual(self, role: str) -> QLineEdit:
        return {
            "semantic": self.semantic_manual,
            "fact": self.fact_manual,
            "web": self.web_manual,
        }[role]

    def _selected_model(self, role: str) -> str:
        manual = self._role_manual(role)
        if manual.isEnabled() and manual.text().strip():
            return manual.text().strip()
        return self._role_combo(role).currentText().strip()

    def _set_combo_value(self, combo: QComboBox, value: str) -> None:
        value = str(value or "").strip()
        combo.blockSignals(True)
        try:
            combo.clear()
            if value:
                combo.addItem(value)
                combo.setCurrentIndex(0)
            else:
                combo.setCurrentIndex(-1)
        finally:
            combo.blockSignals(False)

    def _apply_catalog_to_role(self, role: str) -> None:
        connection = self._connection_for_role(role)
        catalog = self._catalogs.get(connection)
        available = self._catalog_available.get(connection)
        combo = self._role_combo(role)
        manual = self._role_manual(role)
        current = self._selected_model(role)

        combo.blockSignals(True)
        manual.blockSignals(True)
        try:
            if available is True and catalog:
                combo.clear()
                combo.addItems(sorted(catalog, key=str.casefold))
                index = combo.findText(current)
                combo.setCurrentIndex(index if index >= 0 else -1)
                manual.clear()
                manual.setEnabled(False)
            elif available is False:
                combo.clear()
                if current:
                    combo.addItem(current)
                    combo.setCurrentIndex(0)
                manual.setEnabled(True)
                manual.setText(current)
            elif available is True and not catalog:
                combo.clear()
                combo.setCurrentIndex(-1)
                manual.clear()
                manual.setEnabled(False)
            else:
                if combo.count() == 0 and current:
                    combo.addItem(current)
                    combo.setCurrentIndex(0)
                manual.setEnabled(False)
        finally:
            combo.blockSignals(False)
            manual.blockSignals(False)

    def _catalog_status_label(self, connection: str) -> QLabel:
        return {
            "main": self.main_catalog_status,
            "fact": self.fact_catalog_status,
            "web": self.web_catalog_status,
        }[connection]

    def _catalog_button(self, connection: str) -> QPushButton:
        return {
            "main": self.main_catalog_button,
            "fact": self.fact_catalog_button,
            "web": self.web_catalog_button,
        }[connection]

    def _start_catalog(self, connection: str) -> None:
        if self._busy:
            return
        if connection == "fact" and not self.fact_override.isChecked():
            QMessageBox.information(self, "Fact 跟随默认连接", "Fact 当前复用默认连接，请测试默认连接即可。")
            return
        if connection == "web" and not self.web_override.isChecked():
            QMessageBox.information(self, "Web 跟随默认连接", "Web 当前复用默认连接，请测试默认连接即可。")
            return
        try:
            base_url = self._connection_base_url(connection)
            api_key = self._effective_key(connection)
            if not api_key:
                raise ValueError("API Key 为空。")
            RoleBinding("fact", base_url, "catalog-probe", api_key).normalized()
        except Exception as exc:
            QMessageBox.warning(self, "无法测试连接", str(exc))
            return

        self._set_busy(True)
        status = self._catalog_status_label(connection)
        status.setText("正在连接 /v1/models…")
        status.setObjectName("aiRoleStatus")
        expected_url = base_url.rstrip("/")

        def worker() -> None:
            result = discover_models(base_url=base_url, api_key=api_key)
            self.catalog_finished.emit((connection, expected_url, result))

        threading.Thread(target=worker, name=f"ai-model-catalog-{connection}", daemon=True).start()

    def _catalog_done(self, payload: object) -> None:
        self._set_busy(False)
        try:
            connection, expected_url, result = payload  # type: ignore[misc]
            assert isinstance(result, ModelCatalogResult)
        except Exception:
            return
        if self._connection_base_url(connection).rstrip("/") != expected_url:
            self._catalog_status_label(connection).setText("连接已变化，本次模型目录结果已丢弃。")
            return
        self._catalog_available[connection] = bool(result.catalog_available)
        self._catalogs[connection] = set(result.models) if result.catalog_available else None
        status = self._catalog_status_label(connection)
        if result.catalog_available and result.models:
            status.setText(f"✓ 连接成功 · 发现 {len(result.models)} 个模型。请选择模型后继续能力验证。")
            status.setObjectName("aiCapabilityPass")
        elif result.catalog_available:
            status.setText("连接成功，但 /models 没有返回模型；该连接暂不能用于自动选择。")
            status.setObjectName("aiCapabilityWarn")
        else:
            status.setText(
                "此服务未提供可用的 /models 目录，可手动填写模型 ID；仍必须通过后面的真实能力验证。\n"
                + result.error
            )
            status.setObjectName("aiCapabilityWarn")
        status.style().unpolish(status)
        status.style().polish(status)
        for role in ("semantic", "fact", "web"):
            if self._connection_for_role(role) == connection:
                self._apply_catalog_to_role(role)
        self._invalidate_verification("模型目录已刷新，请验证所选模型能力")

    # Capability verification ----------------------------------------------
    def _bindings_from_ui(self) -> tuple[RoleBinding, RoleBinding, RoleBinding]:
        main_key = self._effective_key("main")
        if not main_key:
            raise CapabilityProbeError("默认 API Key 为空。")
        fact_key = self._effective_key("fact") if self.fact_override.isChecked() else main_key
        web_key = self._effective_key("web") if self.web_override.isChecked() else main_key
        fact_url = self.fact_base_url.text().strip() if self.fact_override.isChecked() else self.base_url.text().strip()
        web_url = self.web_base_url.text().strip() if self.web_override.isChecked() else self.base_url.text().strip()
        bindings = (
            RoleBinding("semantic", self.base_url.text().strip(), self._selected_model("semantic"), main_key).normalized(),
            RoleBinding("fact", fact_url, self._selected_model("fact"), fact_key).normalized(),
            RoleBinding("web", web_url, self._selected_model("web"), web_key).normalized(),
        )
        for binding in bindings:
            connection = self._connection_for_role(binding.role)
            catalog = self._catalogs.get(connection)
            available = self._catalog_available.get(connection)
            if available is True and catalog is not None and binding.model not in catalog:
                raise CapabilityProbeError(
                    f"{_ROLE_LABELS[binding.role]} 模型 {binding.model!r} 不在该 Key 返回的 /models 目录中。"
                )
        return bindings

    def _start_capability_probe(self) -> None:
        if self._busy:
            return
        try:
            bindings = self._bindings_from_ui()
        except Exception as exc:
            QMessageBox.warning(self, "无法开始能力验证", str(exc))
            return
        self._set_busy(True)
        for role in ("semantic", "fact", "web"):
            self._set_capability_status(role, "testing", "正在执行真实能力测试…")
        signatures = {binding.role: binding_signature(binding) for binding in bindings}

        def worker() -> None:
            reports: list[RoleCapabilityReport] = []
            for binding in bindings:
                report = probe_role(binding)
                reports.append(report)
                if not report.passed:
                    break
            self.probe_finished.emit((bindings, signatures, tuple(reports)))

        threading.Thread(target=worker, name="ai-capability-preflight", daemon=True).start()

    def _probe_done(self, payload: object) -> None:
        self._set_busy(False)
        try:
            bindings, signatures, reports = payload  # type: ignore[misc]
            current = self._bindings_from_ui()
        except Exception as exc:
            self._invalidate_verification("配置在验证期间已经变化")
            QMessageBox.warning(self, "能力验证结果已失效", str(exc))
            return
        current_signatures = {binding.role: binding_signature(binding) for binding in current}
        if current_signatures != signatures:
            self._invalidate_verification("配置在验证期间已经变化，请重新验证")
            return

        report_by_role = {report.role: report for report in reports}
        self._validated_reports = report_by_role
        self._validated_signatures = dict(signatures)
        failed = False
        for role in ("semantic", "fact", "web"):
            report = report_by_role.get(role)
            if report is None:
                failed = True
                self._set_capability_status(role, "fail", "前置角色验证失败，本角色未继续测试。")
                continue
            if report.passed:
                details = " · ".join(check.name for check in report.checks if check.passed)
                self._set_capability_status(role, "pass", "验证通过 · " + details)
            else:
                failed = True
                detail = report.error or "; ".join(
                    check.detail for check in report.checks if not check.passed and check.detail
                )
                self._set_capability_status(role, "fail", "验证失败 · " + detail)
        if failed:
            self._validated_signatures.clear()
            QMessageBox.warning(
                self,
                "模型能力不匹配",
                "至少一个角色没有通过真实能力校验。该配置不能保存为新的托管 AI 配置，也不会进入上架流程。",
            )
        else:
            QMessageBox.information(
                self,
                "能力验证通过",
                "主语义 / Fact / Web 三个角色均已通过真实能力验证。现在可以安全保存配置。",
            )

    def _set_capability_status(self, role: str, state: str, text: str) -> None:
        label = self.capability_status[role]
        label.setText(text)
        label.setObjectName(
            "aiCapabilityPass" if state == "pass" else "aiCapabilityFail" if state == "fail" else "aiCapabilityWarn"
        )
        label.style().unpolish(label)
        label.style().polish(label)

    def _invalidate_verification(self, reason: str) -> None:
        if self._suppress_dirty:
            return
        self._validated_reports.clear()
        self._validated_signatures.clear()
        for role in ("semantic", "fact", "web"):
            self._set_capability_status(role, "testing", f"需要重新验证 · {reason}")

    def _configuration_changed(self, *_args: object) -> None:
        self._invalidate_verification("连接或模型已变化")

    def _override_changed(self, *_args: object) -> None:
        self._sync_override_controls()
        for role in ("fact", "web"):
            self._apply_catalog_to_role(role)
        self._invalidate_verification("角色连接方式已变化")

    # Persistence/status -----------------------------------------------------
    def _settings_from_ui(self) -> AIServiceSettings:
        return AIServiceSettings(
            provider="openai-compatible",
            base_url=self.base_url.text().strip(),
            model=self._selected_model("semantic"),
            fact_model=self._selected_model("fact"),
            web_model=self._selected_model("web"),
            fact_override_enabled=self.fact_override.isChecked(),
            fact_base_url=self.fact_base_url.text().strip(),
            web_override_enabled=self.web_override.isChecked(),
            web_base_url=self.web_base_url.text().strip(),
        ).validated()

    def _candidate_changed(self, settings: AIServiceSettings) -> bool:
        if settings != self._loaded_settings:
            return True
        if self._editing_key and self.api_key.text().strip() not in {"", _MASKED_KEY}:
            return True
        if settings.fact_override_enabled and self._role_key_editing["fact"] and self.fact_key.text().strip() not in {"", _MASKED_KEY}:
            return True
        if settings.web_override_enabled and self._role_key_editing["web"] and self.web_key.text().strip() not in {"", _MASKED_KEY}:
            return True
        return False

    def _load_persisted_verification(self) -> None:
        try:
            snapshot = load_verification_snapshot(config_dir=settings_directory())
        except Exception as exc:
            self._persisted_snapshot_present = True
            for role in ("semantic", "fact", "web"):
                self._set_capability_status(role, "fail", f"验证记录损坏 · {exc}")
            return
        self._persisted_snapshot_present = snapshot is not None
        if snapshot is None:
            for role in ("semantic", "fact", "web"):
                self._set_capability_status(
                    role,
                    "testing",
                    "旧版已保存配置 · 尚无能力验证记录；保持兼容，但修改连接/模型后必须先验证。",
                )
            return
        try:
            bindings = self._bindings_from_ui()
        except Exception as exc:
            for role in ("semantic", "fact", "web"):
                self._set_capability_status(role, "fail", f"已保存验证无法匹配当前配置 · {exc}")
            return
        records = snapshot.by_role()
        current_signatures = {binding.role: binding_signature(binding) for binding in bindings}
        if any(
            role not in records
            or not records[role].passed
            or records[role].binding_signature != current_signatures[role]
            for role in ("semantic", "fact", "web")
        ):
            for role in ("semantic", "fact", "web"):
                self._set_capability_status(role, "fail", "已保存能力验证与当前 URL / Key / 模型不匹配，请重新验证。")
            return
        self._validated_signatures = current_signatures
        self._validated_reports = {
            role: RoleCapabilityReport(
                role=role,
                base_url=records[role].base_url,
                model=records[role].model,
                passed=True,
                checks=records[role].checks,
            )
            for role in ("semantic", "fact", "web")
        }
        for role in ("semantic", "fact", "web"):
            self._set_capability_status(role, "pass", f"✓ 已验证 · {records[role].model}")

    def reload(self) -> None:
        self._suppress_dirty = True
        try:
            try:
                settings = load_ai_service_settings()
            except Exception as exc:
                QMessageBox.warning(self, "AI 设置读取失败", str(exc))
                settings = AIServiceSettings()
            self._loaded_settings = settings
            self.base_url.setText(settings.base_url)
            self.fact_base_url.setText(settings.fact_base_url)
            self.web_base_url.setText(settings.web_base_url)
            self.fact_override.setChecked(settings.fact_override_enabled)
            self.web_override.setChecked(settings.web_override_enabled)
            self._set_combo_value(self.model, settings.model)
            self._set_combo_value(self.fact_model, settings.fact_model)
            self._set_combo_value(self.web_model, settings.web_model)
            self.semantic_manual.clear()
            self.fact_manual.clear()
            self.web_manual.clear()
            self._set_main_key_state(has_ai_service_key(settings))
            self._set_role_key_state("fact", has_ai_service_role_key("fact"))
            self._set_role_key_state("web", has_ai_service_role_key("web"))
            self._catalogs = {"main": None, "fact": None, "web": None}
            self._catalog_available = {"main": None, "fact": None, "web": None}
            self.main_catalog_status.setText("尚未读取模型目录。")
            self.fact_catalog_status.setText("尚未读取模型目录。")
            self.web_catalog_status.setText("尚未读取模型目录。")
            self._sync_override_controls()
            self._sync_image_optimization()
        finally:
            self._suppress_dirty = False
        self._load_persisted_verification()

    def _sync_override_controls(self) -> None:
        fact_enabled = self.fact_override.isChecked()
        web_enabled = self.web_override.isChecked()
        for widget in (self.fact_base_url, self.fact_key, self.fact_key_action, self.fact_catalog_button, self.fact_catalog_status):
            widget.setEnabled(fact_enabled)
        for widget in (self.web_base_url, self.web_key, self.web_key_action, self.web_catalog_button, self.web_catalog_status):
            widget.setEnabled(web_enabled)

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        for button in (
            self.main_catalog_button,
            self.fact_catalog_button,
            self.web_catalog_button,
            self.verify_button,
            self.save_button,
        ):
            button.setEnabled(not busy)
        self._sync_override_controls()
        if busy:
            self.fact_catalog_button.setEnabled(False)
            self.web_catalog_button.setEnabled(False)

    def _save(self) -> None:
        try:
            settings = self._settings_from_ui()
            bindings = self._bindings_from_ui()
        except Exception as exc:
            QMessageBox.warning(self, "AI 设置不完整", str(exc))
            return

        changed = self._candidate_changed(settings)
        signatures = {binding.role: binding_signature(binding) for binding in bindings}
        in_memory_valid = (
            len(self._validated_signatures) == 3
            and self._validated_signatures == signatures
            and all(report.passed for report in self._validated_reports.values())
        )

        if changed and not in_memory_valid:
            QMessageBox.warning(
                self,
                "请先验证模型能力",
                "连接、API Key 或模型已经变化。请先点击“验证所选模型能力”；只有通过真实能力校验后才能保存新的配置。",
            )
            return
        if not changed and self._persisted_snapshot_present and not in_memory_valid:
            QMessageBox.warning(
                self,
                "能力验证已失效",
                "当前托管配置的验证记录与实际连接不匹配，请重新验证后再保存。",
            )
            return

        main_key: str | None = None
        if self._editing_key:
            value = self.api_key.text().strip()
            if not value or value == _MASKED_KEY:
                if not self._key_configured:
                    QMessageBox.warning(self, "API Key 未配置", "请填写默认连接 API Key。")
                    return
            else:
                main_key = value

        role_keys: dict[str, str | None] = {"fact": None, "web": None}
        for role, enabled, editor in (
            ("fact", settings.fact_override_enabled, self.fact_key),
            ("web", settings.web_override_enabled, self.web_key),
        ):
            if not enabled:
                continue
            if self._role_key_editing[role]:
                value = editor.text().strip()
                if value and value != _MASKED_KEY:
                    role_keys[role] = value
                elif not self._role_key_configured[role]:
                    QMessageBox.warning(self, f"{role.title()} API Key 未配置", "独立连接必须配置自己的 API Key。")
                    return

        try:
            if main_key is not None:
                # save_ai_service_settings writes the main key atomically with settings.
                pass
            for role, value in role_keys.items():
                if value is not None:
                    save_ai_service_role_key(role, value)
            normalized = save_ai_service_settings(settings, api_key=main_key)
            if not has_ai_service_key(normalized):
                raise CapabilityProbeError("默认 API Key 仍为空。")
            if in_memory_valid:
                # Re-resolve keys after persistence so the verification record is tied
                # to the exact credentials the runtime will load later.
                final_main = load_ai_service_key(normalized)
                final_fact = load_ai_service_role_key("fact") if normalized.fact_override_enabled else final_main
                final_web = load_ai_service_role_key("web") if normalized.web_override_enabled else final_main
                final_bindings = (
                    RoleBinding("semantic", normalized.base_url, normalized.model, final_main),
                    RoleBinding(
                        "fact",
                        normalized.fact_base_url if normalized.fact_override_enabled else normalized.base_url,
                        normalized.fact_model,
                        final_fact,
                    ),
                    RoleBinding(
                        "web",
                        normalized.web_base_url if normalized.web_override_enabled else normalized.base_url,
                        normalized.web_model,
                        final_web,
                    ),
                )
                final_signatures = {item.role: binding_signature(item) for item in final_bindings}
                if final_signatures != signatures:
                    raise CapabilityProbeError("保存后的连接签名与刚才验证的配置不一致，请重新验证。")
                save_verification_snapshot(
                    config_dir=settings_directory(),
                    bindings=final_bindings,
                    reports=tuple(self._validated_reports[role] for role in ("semantic", "fact", "web")),
                )
        except Exception as exc:
            QMessageBox.critical(self, "AI 设置无法保存", str(exc))
            return

        self.reload()
        QMessageBox.information(self, "AI 设置已保存", "连接、模型与能力验证状态已经保存。")

    def _clear_key(self) -> None:
        answer = QMessageBox.question(
            self,
            "清除默认 API Key",
            "确定删除本机保存的默认 AI API Key？这会同时清除能力验证记录；独立 Fact / Web 密钥不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        clear_ai_service_key()
        clear_verification_snapshot(config_dir=settings_directory())
        os.environ.pop(_RUNTIME_KEY_ENV, None)
        self.reload()

    # Image optimization ----------------------------------------------------
    def _sync_image_optimization(self) -> None:
        enabled = bool(_IMAGE_OPTIMIZATION_GUI_UNLOCKED and image_optimization_enabled())
        blocked = self.image_optimization.blockSignals(True)
        try:
            self.image_optimization.setChecked(enabled)
        finally:
            self.image_optimization.blockSignals(blocked)
        self.image_optimization.setEnabled(_IMAGE_OPTIMIZATION_GUI_UNLOCKED)
        self.image_optimization_state.setText(
            "已开启 · 使用高负载 AI 图片链"
            if enabled
            else "默认关闭 · 高负载 · 当前暂未开放"
        )

    def _image_optimization_toggled(self, checked: bool) -> None:
        enabled = bool(checked and _IMAGE_OPTIMIZATION_GUI_UNLOCKED)
        set_image_optimization_enabled(enabled)
        if checked != enabled:
            self._sync_image_optimization()


class AISettingsModalController:
    """Bind verified AI connection settings to Single and Batch runtimes."""

    def __init__(self, window) -> None:
        self.window = window
        set_image_optimization_enabled(False)
        self.button = QPushButton("设置")
        self.button.setObjectName("quietButton")
        self.button.setToolTip("AI 连接 / 模型发现 / 能力验证")
        self.button.clicked.connect(self.open)
        self._panel: AISettingsContent | None = None
        self._runner_start: Callable[..., Any] | None = None
        self._batch_start_prepare: Callable[..., Any] | None = None
        self._install_header_button()
        self._install_runtime_binding()

    def open(self) -> None:
        details = getattr(self.window, "_card_details", None)
        open_custom = getattr(details, "open_custom", None)
        body_layout = getattr(details, "body_layout", None)
        body = getattr(details, "body", None)
        if not callable(open_custom) or body_layout is None or not isinstance(body, QWidget):
            QMessageBox.warning(self.window, "设置无法打开", "详情弹窗组件尚未初始化。")
            return

        def populate() -> None:
            panel = AISettingsContent(body)
            self._panel = panel
            body_layout.addWidget(panel, 1)

        open_custom(
            title="AI 服务设置",
            eyebrow="SETTINGS · AI CONNECTIONS",
            populate=populate,
            ratio=(0.76, 0.86),
        )

    def _install_header_button(self) -> None:
        root = self.window.centralWidget()
        outer = root.layout() if root is not None else None
        header_item = outer.itemAt(0) if outer is not None and outer.count() else None
        header = header_item.layout() if header_item is not None else None
        if not isinstance(header, QHBoxLayout):
            raise RuntimeError("AI settings expected the common application header")
        header.addWidget(self.button, 0, Qt.AlignmentFlag.AlignBottom)

    @staticmethod
    def _clear_role_runtime() -> None:
        for name in (
            RUNTIME_FACT_BASE_URL_ENV,
            RUNTIME_FACT_KEY_ENV,
            RUNTIME_WEB_BASE_URL_ENV,
            RUNTIME_WEB_KEY_ENV,
        ):
            os.environ.pop(name, None)

    @staticmethod
    def _persisted_bindings(settings: AIServiceSettings, main_key: str) -> tuple[RoleBinding, RoleBinding, RoleBinding]:
        fact_key = load_ai_service_role_key("fact") if settings.fact_override_enabled else main_key
        web_key = load_ai_service_role_key("web") if settings.web_override_enabled else main_key
        if settings.fact_override_enabled and not fact_key:
            raise CapabilityProbeError("Fact 独立连接已启用，但没有可用的 Fact API Key。")
        if settings.web_override_enabled and not web_key:
            raise CapabilityProbeError("Web 独立连接已启用，但没有可用的 Web API Key。")
        return (
            RoleBinding("semantic", settings.base_url, settings.model, main_key).normalized(),
            RoleBinding(
                "fact",
                settings.fact_base_url if settings.fact_override_enabled else settings.base_url,
                settings.fact_model,
                fact_key,
            ).normalized(),
            RoleBinding(
                "web",
                settings.web_base_url if settings.web_override_enabled else settings.base_url,
                settings.web_model,
                web_key,
            ).normalized(),
        )

    def _apply_runtime(self, config) -> None:
        settings, api_key = resolved_ai_runtime()
        bindings = self._persisted_bindings(settings, api_key)
        # A verification snapshot turns the configuration into managed mode.
        # URL/key/model drift is rejected here before any browser/listing work starts.
        assert_verified_if_managed(config_dir=settings_directory(), bindings=bindings)

        config.provider = "openai-compatible"
        config.base_url = settings.base_url
        config.local_model = settings.model
        config.fact_model = settings.fact_model
        config.web_model = settings.web_model
        config.api_key_env = _RUNTIME_KEY_ENV
        os.environ[_RUNTIME_KEY_ENV] = api_key

        self._clear_role_runtime()
        if settings.fact_override_enabled:
            os.environ[RUNTIME_FACT_BASE_URL_ENV] = settings.fact_base_url
            os.environ[RUNTIME_FACT_KEY_ENV] = bindings[1].api_key
        if settings.web_override_enabled:
            os.environ[RUNTIME_WEB_BASE_URL_ENV] = settings.web_base_url
            os.environ[RUNTIME_WEB_KEY_ENV] = bindings[2].api_key

    def _install_runtime_binding(self) -> None:
        runner = self.window.runner
        self._runner_start = runner.start

        def single_start(config, *args, **kwargs):
            self._apply_runtime(config)
            assert self._runner_start is not None
            return self._runner_start(config, *args, **kwargs)

        runner.start = single_start

        batch_workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(batch_workspace, "controller", None)
        if controller is None:
            raise RuntimeError("AI settings requires installed Batch workspace")
        self._batch_start_prepare = controller.start_prepare

        def batch_start_prepare(urls, config, *args, **kwargs):
            self._apply_runtime(config)
            assert self._batch_start_prepare is not None
            return self._batch_start_prepare(urls, config, *args, **kwargs)

        controller.start_prepare = batch_start_prepare


def install_ai_settings_modal(window) -> AISettingsModalController:
    existing = getattr(window, "_ai_settings_controller", None)
    if isinstance(existing, AISettingsModalController):
        return existing
    controller = AISettingsModalController(window)
    window._ai_settings_controller = controller
    return controller


__all__ = ["AISettingsContent", "AISettingsModalController", "install_ai_settings_modal"]
