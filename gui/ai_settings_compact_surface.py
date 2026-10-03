from __future__ import annotations

import os
import threading
from typing import Any

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
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
    discover_models,
    load_verification_snapshot,
    probe_role,
    save_verification_snapshot,
)
from app.ai_profile_store import (
    AI_SOURCE_QWEN,
    AI_SOURCE_RELAY,
    RelayAIProfile,
    has_relay_key,
    load_active_ai_source,
    load_qwen_profile,
    load_relay_key,
    load_relay_profile,
    relay_verification_directory,
    save_active_ai_source,
    save_relay_key,
    save_relay_profile,
)
from app.ai_service_settings import (
    RUNTIME_FACT_BASE_URL_ENV,
    RUNTIME_FACT_KEY_ENV,
    RUNTIME_WEB_BASE_URL_ENV,
    RUNTIME_WEB_KEY_ENV,
    has_ai_service_key,
    load_ai_service_key,
    load_ai_service_settings,
    save_ai_service_key,
)
from .ai_settings_surface import AISettingsModalController as _BaseAISettingsModalController


_RUNTIME_KEY_ENV = "AI_API_KEY"
_MASKED_KEY = "••••••••••••••••"
_ROLE_LABELS = {"semantic": "主语义", "fact": "Fact", "web": "Web"}
_ROLE_REQUIREMENT_TEXT = {
    "semantic": "Strict JSON Schema + Vision 真读图",
    "fact": "Strict JSON Schema",
    "web": "Responses API + web_search + 来源 URL",
}

_STYLE = r"""
QWidget#aiProfileSettings { background: transparent; }
QLabel#aiHint { color: rgba(255,255,255,188); font-size: 11px; }
QLabel#aiProfileTitle { color: rgba(255,255,255,242); font-size: 13px; font-weight: 760; }
QLabel#aiProfileMeta { color: rgba(255,255,255,142); font-size: 10px; }
QLabel#aiPass { color: #9fe2bd; font-size: 10px; font-weight: 680; }
QLabel#aiWarn { color: #f4cb7a; font-size: 10px; font-weight: 680; }
QLabel#aiFail { color: #f1a0ac; font-size: 10px; font-weight: 680; }
QLineEdit#aiInput, QComboBox#modalCombo {
    min-height: 39px;
    padding: 0 12px;
    color: rgba(255,255,255,238);
    background-color: rgba(10,20,32,88);
    border: 1px solid rgba(255,255,255,24);
    border-radius: 8px;
}
QLineEdit#aiInput:read-only {
    color: rgba(255,255,255,178);
    background-color: rgba(10,20,32,56);
}
"""


class AISettingsContent(QWidget):
    """Two isolated AI profiles: proven official Qwen and user relay gateway."""

    catalog_finished = Signal(object)
    probe_finished = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiProfileSettings")
        self.setStyleSheet(_STYLE)

        self._busy = False
        self._qwen_key_editing = False
        self._relay_key_editing = False
        self._qwen_key_configured = False
        self._relay_key_configured = False
        self._relay_catalog_available: bool | None = None
        self._relay_catalog: set[str] | None = None
        self._relay_reports: dict[str, RoleCapabilityReport] = {}
        self._relay_signatures: dict[str, str] = {}
        self._loaded_relay: RelayAIProfile | None = None

        self.catalog_finished.connect(self._catalog_done)
        self.probe_finished.connect(self._probe_done)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        intro = QLabel(
            "官方 Qwen 和中转站是两套完全独立的配置。切换来源不会覆盖另一套 URL、Key 或模型。"
        )
        intro.setObjectName("aiHint")
        intro.setWordWrap(True)
        root.addWidget(intro)

        source_card = QFrame()
        source_card.setObjectName("cardDetailSection")
        source_layout = QGridLayout(source_card)
        source_layout.setContentsMargins(15, 13, 15, 13)
        source_layout.setHorizontalSpacing(14)
        source_layout.setColumnStretch(1, 1)
        source_label = QLabel("当前模型来源")
        source_label.setObjectName("modalFieldLabel")
        self.source = QComboBox()
        self.source.setObjectName("modalCombo")
        self.source.addItem("官方 Qwen（稳定配置）", AI_SOURCE_QWEN)
        self.source.addItem("中转站", AI_SOURCE_RELAY)
        self.source.currentIndexChanged.connect(self._source_changed)
        source_layout.addWidget(source_label, 0, 0)
        source_layout.addWidget(self.source, 0, 1)
        root.addWidget(source_card)

        self.qwen_card = self._build_qwen_card()
        self.relay_card = self._build_relay_card()
        root.addWidget(self.qwen_card)
        root.addWidget(self.relay_card)

        self.save_button = QPushButton("保存并使用当前来源")
        self.save_button.setObjectName("modalPrimaryButton")
        self.save_button.clicked.connect(self._save_current)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.save_button)
        root.addLayout(actions)

        self.reload()

    # ------------------------------------------------------------------ UI
    @staticmethod
    def _input(placeholder: str, *, read_only: bool = False) -> QLineEdit:
        editor = QLineEdit()
        editor.setObjectName("aiInput")
        editor.setPlaceholderText(placeholder)
        editor.setReadOnly(read_only)
        return editor

    @staticmethod
    def _status(text: str = "") -> QLabel:
        label = QLabel(text)
        label.setObjectName("aiProfileMeta")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _field(form: QGridLayout, row: int, label: str, widget: QWidget) -> None:
        title = QLabel(label)
        title.setObjectName("modalFieldLabel")
        form.addWidget(title, row, 0, Qt.AlignmentFlag.AlignVCenter)
        form.addWidget(widget, row, 1)

    @staticmethod
    def _key_row(editor: QLineEdit, button: QPushButton) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(editor, 1)
        layout.addWidget(button)
        return row

    def _build_qwen_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("cardDetailSection")
        form = QGridLayout(card)
        form.setContentsMargins(15, 14, 15, 15)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)

        title = QLabel("官方 Qwen · 原有稳定配置")
        title.setObjectName("aiProfileTitle")
        form.addWidget(title, 0, 0, 1, 2)
        note = QLabel("这套配置沿用你原来已经跑通的 DashScope/Qwen，不会被中转站测试覆盖。")
        note.setObjectName("aiHint")
        note.setWordWrap(True)
        form.addWidget(note, 1, 0, 1, 2)

        self.qwen_base = self._input("", read_only=True)
        self.qwen_semantic = self._input("", read_only=True)
        self.qwen_fact = self._input("", read_only=True)
        self.qwen_web = self._input("", read_only=True)
        self.qwen_key = self._input("官方 Qwen API Key")
        self.qwen_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.qwen_key_action = QPushButton("更改")
        self.qwen_key_action.setObjectName("modalPrimaryButton")
        self.qwen_key_action.setMaximumWidth(72)
        self.qwen_key_action.clicked.connect(self._qwen_key_action)
        self.qwen_status = self._status()

        self._field(form, 2, "Base URL", self.qwen_base)
        self._field(form, 3, "主语义", self.qwen_semantic)
        self._field(form, 4, "Fact", self.qwen_fact)
        self._field(form, 5, "Web", self.qwen_web)
        self._field(form, 6, "API Key", self._key_row(self.qwen_key, self.qwen_key_action))
        form.addWidget(self.qwen_status, 7, 1)
        return card

    def _build_relay_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("cardDetailSection")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 14, 15, 15)
        layout.setSpacing(12)

        title = QLabel("中转站 · 独立配置")
        title.setObjectName("aiProfileTitle")
        layout.addWidget(title)
        hint = QLabel(
            "流程：填 Base URL + Key → 探测可用模型 → 为三个角色选择模型 → 测试能力 → 保存并切换。"
        )
        hint.setObjectName("aiHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        connection = QFrame()
        connection.setObjectName("cardDetailSection")
        form = QGridLayout(connection)
        form.setContentsMargins(12, 12, 12, 12)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)
        form.setColumnStretch(1, 1)

        self.relay_base = self._input("https://your-relay.example/v1")
        self.relay_key = self._input("中转站 API Key")
        self.relay_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.relay_key_action = QPushButton("显示")
        self.relay_key_action.setObjectName("modalPrimaryButton")
        self.relay_key_action.setMaximumWidth(72)
        self.relay_key_action.clicked.connect(self._relay_key_action)
        self.catalog_button = QPushButton("探测连接并读取可用模型")
        self.catalog_button.setObjectName("modalPrimaryButton")
        self.catalog_button.clicked.connect(self._start_catalog)
        self.catalog_status = self._status("尚未探测模型。")

        self._field(form, 0, "Base URL", self.relay_base)
        self._field(form, 1, "API Key", self._key_row(self.relay_key, self.relay_key_action))
        form.addWidget(self.catalog_button, 2, 1)
        form.addWidget(self.catalog_status, 3, 1)
        layout.addWidget(connection)

        roles = QFrame()
        roles.setObjectName("cardDetailSection")
        role_form = QGridLayout(roles)
        role_form.setContentsMargins(12, 12, 12, 12)
        role_form.setHorizontalSpacing(14)
        role_form.setVerticalSpacing(9)
        role_form.setColumnStretch(1, 1)

        self.relay_models: dict[str, QComboBox] = {}
        self.relay_manual: dict[str, QLineEdit] = {}
        for row, role in enumerate(("semantic", "fact", "web")):
            combo = QComboBox()
            combo.setObjectName("modalCombo")
            combo.currentIndexChanged.connect(self._relay_changed)
            manual = self._input("当中转站不提供 /models 时手动填写模型 ID")
            manual.textChanged.connect(self._relay_changed)
            manual.hide()
            self.relay_models[role] = combo
            self.relay_manual[role] = manual
            self._field(role_form, row * 2, _ROLE_LABELS[role], combo)
            manual_label = QLabel("手动模型 ID")
            manual_label.setObjectName("modalFieldLabel")
            manual_label.setProperty("relayManualRole", role)
            manual_label.hide()
            role_form.addWidget(manual_label, row * 2 + 1, 0)
            role_form.addWidget(manual, row * 2 + 1, 1)

        layout.addWidget(roles)

        capability = QFrame()
        capability.setObjectName("cardDetailSection")
        cap_form = QGridLayout(capability)
        cap_form.setContentsMargins(12, 12, 12, 12)
        cap_form.setHorizontalSpacing(14)
        cap_form.setVerticalSpacing(8)
        cap_form.setColumnStretch(1, 1)
        self.capability_status: dict[str, QLabel] = {}
        for row, role in enumerate(("semantic", "fact", "web")):
            name = QLabel(_ROLE_LABELS[role])
            name.setObjectName("modalFieldLabel")
            status = self._status("尚未验证 · " + _ROLE_REQUIREMENT_TEXT[role])
            self.capability_status[role] = status
            cap_form.addWidget(name, row, 0)
            cap_form.addWidget(status, row, 1)
        self.verify_button = QPushButton("测试所选模型能力")
        self.verify_button.setObjectName("modalPrimaryButton")
        self.verify_button.clicked.connect(self._start_probe)
        cap_form.addWidget(self.verify_button, 3, 1)
        layout.addWidget(capability)

        return card

    # --------------------------------------------------------------- reload
    def reload(self) -> None:
        try:
            qwen, _ = load_qwen_profile()
            self.qwen_base.setText(qwen.base_url)
            self.qwen_semantic.setText(qwen.model)
            self.qwen_fact.setText(qwen.fact_model)
            self.qwen_web.setText(qwen.web_model)
            self._qwen_key_configured = has_ai_service_key(qwen)
        except Exception:
            qwen = load_ai_service_settings()
            self.qwen_base.setText(qwen.base_url)
            self.qwen_semantic.setText(qwen.model)
            self.qwen_fact.setText(qwen.fact_model)
            self.qwen_web.setText(qwen.web_model)
            self._qwen_key_configured = has_ai_service_key(qwen)

        self._qwen_key_editing = not self._qwen_key_configured
        if self._qwen_key_configured:
            self.qwen_key.setReadOnly(True)
            self.qwen_key.setText(_MASKED_KEY)
            self.qwen_key_action.setText("更改")
            self.qwen_status.setText("✓ 原有 Qwen Key 已安全保存。")
            self.qwen_status.setObjectName("aiPass")
        else:
            self.qwen_key.setReadOnly(False)
            self.qwen_key.clear()
            self.qwen_key_action.setText("显示")
            self.qwen_status.setText("尚未配置官方 Qwen Key。")
            self.qwen_status.setObjectName("aiWarn")

        self._loaded_relay = load_relay_profile()
        self._relay_key_configured = has_relay_key()
        self._relay_key_editing = not self._relay_key_configured
        if self._loaded_relay is not None:
            self.relay_base.setText(self._loaded_relay.base_url)
            for role, value in (
                ("semantic", self._loaded_relay.model),
                ("fact", self._loaded_relay.fact_model),
                ("web", self._loaded_relay.web_model),
            ):
                combo = self.relay_models[role]
                combo.blockSignals(True)
                combo.clear()
                combo.addItem(value)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
        else:
            self.relay_base.clear()
            for combo in self.relay_models.values():
                combo.clear()

        if self._relay_key_configured:
            self.relay_key.setReadOnly(True)
            self.relay_key.setText(_MASKED_KEY)
            self.relay_key_action.setText("更改")
        else:
            self.relay_key.setReadOnly(False)
            self.relay_key.clear()
            self.relay_key_action.setText("显示")

        self._relay_catalog_available = None
        self._relay_catalog = None
        self.catalog_status.setText("尚未探测模型。")
        self._load_relay_verification()

        active = load_active_ai_source()
        index = self.source.findData(active)
        self.source.blockSignals(True)
        self.source.setCurrentIndex(index if index >= 0 else 0)
        self.source.blockSignals(False)
        self._source_changed()

    def _load_relay_verification(self) -> None:
        self._relay_reports.clear()
        self._relay_signatures.clear()
        try:
            snapshot = load_verification_snapshot(config_dir=relay_verification_directory())
            bindings = self._relay_bindings(require_catalog=False)
        except Exception:
            snapshot = None
            bindings = ()
        if snapshot is None or not bindings:
            for role in ("semantic", "fact", "web"):
                self._set_capability(role, "warn", "尚未验证 · " + _ROLE_REQUIREMENT_TEXT[role])
            return
        records = snapshot.by_role()
        signatures = {item.role: binding_signature(item) for item in bindings}
        if any(
            role not in records
            or not records[role].passed
            or records[role].binding_signature != signatures[role]
            for role in ("semantic", "fact", "web")
        ):
            for role in ("semantic", "fact", "web"):
                self._set_capability(role, "warn", "已保存验证与当前中转配置不匹配，请重新测试。")
            return
        self._relay_signatures = signatures
        self._relay_reports = {
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
            self._set_capability(role, "pass", f"✓ 已验证 · {records[role].model}")

    # --------------------------------------------------------------- source
    def _source_changed(self, *_args: object) -> None:
        source = self.source.currentData()
        self.qwen_card.setVisible(source == AI_SOURCE_QWEN)
        self.relay_card.setVisible(source == AI_SOURCE_RELAY)
        self.save_button.setText(
            "保存并使用官方 Qwen" if source == AI_SOURCE_QWEN else "保存并使用中转站"
        )

    # ---------------------------------------------------------------- keys
    def _qwen_key_action(self) -> None:
        if self._qwen_key_configured and not self._qwen_key_editing:
            self._qwen_key_editing = True
            self.qwen_key.setReadOnly(False)
            self.qwen_key.clear()
            self.qwen_key.setPlaceholderText("输入新的官方 Qwen API Key")
            self.qwen_key_action.setText("显示")
            return
        visible = self.qwen_key.echoMode() == QLineEdit.EchoMode.Password
        self.qwen_key.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.qwen_key_action.setText("隐藏" if visible else "显示")

    def _relay_key_action(self) -> None:
        if self._relay_key_configured and not self._relay_key_editing:
            self._relay_key_editing = True
            self.relay_key.setReadOnly(False)
            self.relay_key.clear()
            self.relay_key.setPlaceholderText("输入新的中转站 API Key")
            self.relay_key_action.setText("显示")
            self._relay_changed()
            return
        visible = self.relay_key.echoMode() == QLineEdit.EchoMode.Password
        self.relay_key.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.relay_key_action.setText("隐藏" if visible else "显示")

    def _effective_relay_key(self) -> str:
        if self._relay_key_editing:
            value = self.relay_key.text().strip()
            if value and value != _MASKED_KEY:
                return value
        return load_relay_key()

    # ------------------------------------------------------------- discovery
    def _start_catalog(self) -> None:
        if self._busy:
            return
        base_url = self.relay_base.text().strip()
        key = self._effective_relay_key()
        if not key:
            QMessageBox.warning(self, "缺少中转站 Key", "请先填写中转站 API Key。")
            return
        try:
            RoleBinding("fact", base_url, "catalog-probe", key).normalized()
        except Exception as exc:
            QMessageBox.warning(self, "中转站连接无效", str(exc))
            return
        self._set_busy(True)
        self.catalog_status.setText("正在读取 /v1/models …")
        expected_url = base_url.rstrip("/")

        def worker() -> None:
            result = discover_models(base_url=base_url, api_key=key)
            self.catalog_finished.emit((expected_url, result))

        threading.Thread(target=worker, name="relay-model-discovery", daemon=True).start()

    def _catalog_done(self, payload: object) -> None:
        self._set_busy(False)
        try:
            expected_url, result = payload  # type: ignore[misc]
            assert isinstance(result, ModelCatalogResult)
        except Exception:
            return
        if self.relay_base.text().strip().rstrip("/") != expected_url:
            self.catalog_status.setText("连接在探测期间发生变化，本次结果已丢弃。")
            return
        self._relay_catalog_available = bool(result.catalog_available)
        self._relay_catalog = set(result.models) if result.catalog_available else None
        if result.catalog_available and result.models:
            self.catalog_status.setText(f"✓ 探测成功 · 发现 {len(result.models)} 个可用模型。")
            self.catalog_status.setObjectName("aiPass")
            self._populate_models(result.models)
            self._show_manual(False)
        elif result.catalog_available:
            self.catalog_status.setText("连接成功，但 /models 没有返回模型。")
            self.catalog_status.setObjectName("aiWarn")
            self._populate_models(())
            self._show_manual(False)
        else:
            self.catalog_status.setText("此中转站不提供 /models；已启用手动模型 ID 回退。\n" + result.error)
            self.catalog_status.setObjectName("aiWarn")
            self._show_manual(True)
        self.catalog_status.style().unpolish(self.catalog_status)
        self.catalog_status.style().polish(self.catalog_status)
        self._invalidate_relay_verification("模型目录已刷新")

    def _populate_models(self, models: tuple[str, ...]) -> None:
        previous = self._relay_selected_models()
        for role, combo in self.relay_models.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(models)
            target = previous.get(role, "")
            index = combo.findText(target)
            if index >= 0:
                combo.setCurrentIndex(index)
            elif combo.count():
                combo.setCurrentIndex(0)
            combo.blockSignals(False)

    def _show_manual(self, visible: bool) -> None:
        for role, editor in self.relay_manual.items():
            editor.setVisible(visible)
            for label in self.findChildren(QLabel):
                if label.property("relayManualRole") == role:
                    label.setVisible(visible)

    # --------------------------------------------------------------- models
    def _relay_selected_models(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for role in ("semantic", "fact", "web"):
            manual = self.relay_manual[role]
            if manual.isVisible() and manual.text().strip():
                result[role] = manual.text().strip()
            else:
                result[role] = self.relay_models[role].currentText().strip()
        return result

    def _relay_bindings(self, *, require_catalog: bool) -> tuple[RoleBinding, RoleBinding, RoleBinding]:
        if require_catalog and self._relay_catalog_available is None:
            raise CapabilityProbeError("请先点击“探测连接并读取可用模型”。")
        key = self._effective_relay_key()
        if not key:
            raise CapabilityProbeError("中转站 API Key 为空。")
        base = self.relay_base.text().strip()
        models = self._relay_selected_models()
        bindings = tuple(
            RoleBinding(role, base, models[role], key).normalized()
            for role in ("semantic", "fact", "web")
        )
        if self._relay_catalog_available is True and self._relay_catalog is not None:
            for binding in bindings:
                if binding.model not in self._relay_catalog:
                    raise CapabilityProbeError(
                        f"{_ROLE_LABELS[binding.role]} 模型 {binding.model!r} 不在该 Key 返回的模型目录中。"
                    )
        return bindings  # type: ignore[return-value]

    def _relay_changed(self, *_args: object) -> None:
        if self._busy:
            return
        self._relay_catalog_available = None if self.sender() in {self.relay_base, self.relay_key} else self._relay_catalog_available
        if self.sender() in {self.relay_base, self.relay_key}:
            self._relay_catalog = None
            self.catalog_status.setText("连接已变化，请重新探测模型。")
        self._invalidate_relay_verification("中转站连接或模型已变化")

    # --------------------------------------------------------------- probes
    def _start_probe(self) -> None:
        if self._busy:
            return
        try:
            bindings = self._relay_bindings(require_catalog=True)
        except Exception as exc:
            QMessageBox.warning(self, "无法开始能力测试", str(exc))
            return
        signatures = {item.role: binding_signature(item) for item in bindings}
        self._set_busy(True)
        for role in ("semantic", "fact", "web"):
            self._set_capability(role, "warn", "正在执行真实能力测试…")

        def worker() -> None:
            reports: list[RoleCapabilityReport] = []
            for binding in bindings:
                report = probe_role(binding)
                reports.append(report)
                if not report.passed:
                    break
            self.probe_finished.emit((bindings, signatures, tuple(reports)))

        threading.Thread(target=worker, name="relay-capability-probe", daemon=True).start()

    def _probe_done(self, payload: object) -> None:
        self._set_busy(False)
        try:
            _bindings, signatures, reports = payload  # type: ignore[misc]
            current = self._relay_bindings(require_catalog=True)
            current_signatures = {item.role: binding_signature(item) for item in current}
        except Exception as exc:
            self._invalidate_relay_verification("能力测试期间配置已变化")
            QMessageBox.warning(self, "能力测试结果已失效", str(exc))
            return
        if current_signatures != signatures:
            self._invalidate_relay_verification("能力测试期间配置已变化")
            return

        report_by_role = {item.role: item for item in reports}
        failed = False
        self._relay_reports = report_by_role
        for role in ("semantic", "fact", "web"):
            report = report_by_role.get(role)
            if report is None:
                failed = True
                self._set_capability(role, "fail", "前置角色失败，本角色未继续测试。")
            elif report.passed:
                names = " · ".join(check.name for check in report.checks if check.passed)
                self._set_capability(role, "pass", "✓ 通过 · " + names)
            else:
                failed = True
                self._set_capability(role, "fail", "✕ 失败 · " + (report.error or "能力不匹配"))
        if failed:
            self._relay_signatures.clear()
            QMessageBox.warning(self, "中转站模型能力不匹配", "至少一个角色没有通过能力测试，不能切换到这套中转站配置。")
        else:
            self._relay_signatures = dict(signatures)
            QMessageBox.information(self, "能力测试通过", "三个角色均通过，可以保存并切换到中转站。")

    def _set_capability(self, role: str, state: str, text: str) -> None:
        label = self.capability_status[role]
        label.setText(text)
        label.setObjectName("aiPass" if state == "pass" else "aiFail" if state == "fail" else "aiWarn")
        label.style().unpolish(label)
        label.style().polish(label)

    def _invalidate_relay_verification(self, reason: str) -> None:
        self._relay_reports.clear()
        self._relay_signatures.clear()
        for role in ("semantic", "fact", "web"):
            self._set_capability(role, "warn", "需要重新验证 · " + reason)

    # --------------------------------------------------------------- saving
    def _save_current(self) -> None:
        source = self.source.currentData()
        if source == AI_SOURCE_QWEN:
            self._save_qwen_source()
        else:
            self._save_relay_source()

    def _save_qwen_source(self) -> None:
        if self._qwen_key_editing:
            value = self.qwen_key.text().strip()
            if not value or value == _MASKED_KEY:
                if not self._qwen_key_configured:
                    QMessageBox.warning(self, "官方 Qwen Key 未配置", "请先填写原来的 DashScope/Qwen API Key。")
                    return
            else:
                try:
                    save_ai_service_key(value)
                except Exception as exc:
                    QMessageBox.critical(self, "Qwen Key 保存失败", str(exc))
                    return
        try:
            load_qwen_profile()
            save_active_ai_source(AI_SOURCE_QWEN)
        except Exception as exc:
            QMessageBox.critical(self, "无法切换到官方 Qwen", str(exc))
            return
        self.reload()
        QMessageBox.information(self, "已切换", "已使用原来的官方 Qwen 配置；中转站配置保持不变。")

    def _save_relay_source(self) -> None:
        try:
            bindings = self._relay_bindings(require_catalog=True)
        except Exception as exc:
            QMessageBox.warning(self, "中转站配置不完整", str(exc))
            return
        signatures = {item.role: binding_signature(item) for item in bindings}
        if self._relay_signatures != signatures or len(self._relay_reports) != 3 or not all(
            report.passed for report in self._relay_reports.values()
        ):
            QMessageBox.warning(self, "请先测试模型能力", "中转站连接或模型尚未通过完整能力测试，不能切换为当前来源。")
            return

        models = self._relay_selected_models()
        profile = RelayAIProfile(
            base_url=self.relay_base.text().strip(),
            model=models["semantic"],
            fact_model=models["fact"],
            web_model=models["web"],
        )
        key = self._effective_relay_key()
        try:
            if self._relay_key_editing:
                save_relay_key(key)
            normalized = save_relay_profile(profile)
            final_key = load_relay_key()
            final_bindings = tuple(
                RoleBinding(role, normalized.base_url, model, final_key).normalized()
                for role, model in (
                    ("semantic", normalized.model),
                    ("fact", normalized.fact_model),
                    ("web", normalized.web_model),
                )
            )
            if {item.role: binding_signature(item) for item in final_bindings} != signatures:
                raise CapabilityProbeError("保存后的中转站配置与刚才通过验证的配置不一致，请重新测试。")
            save_verification_snapshot(
                config_dir=relay_verification_directory(),
                bindings=final_bindings,
                reports=tuple(self._relay_reports[role] for role in ("semantic", "fact", "web")),
            )
            save_active_ai_source(AI_SOURCE_RELAY)
        except Exception as exc:
            QMessageBox.critical(self, "中转站配置保存失败", str(exc))
            return
        self.reload()
        QMessageBox.information(self, "已切换", "已保存并使用中转站；原来的官方 Qwen 配置未被修改。")

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        for button in (self.catalog_button, self.verify_button, self.save_button):
            button.setEnabled(not busy)


class AISettingsModalController(_BaseAISettingsModalController):
    """Runtime binding that switches between isolated Qwen and relay profiles."""

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
            eyebrow="SETTINGS · AI PROFILES",
            populate=populate,
            ratio=(0.70, 0.82),
        )

    @staticmethod
    def _clear_role_runtime() -> None:
        for name in (
            RUNTIME_FACT_BASE_URL_ENV,
            RUNTIME_FACT_KEY_ENV,
            RUNTIME_WEB_BASE_URL_ENV,
            RUNTIME_WEB_KEY_ENV,
        ):
            os.environ.pop(name, None)

    def _apply_runtime(self, config) -> None:
        source = load_active_ai_source()
        self._clear_role_runtime()

        if source == AI_SOURCE_QWEN:
            settings, key = load_qwen_profile()
            config.provider = "openai-compatible"
            config.base_url = settings.base_url
            config.local_model = settings.model
            config.fact_model = settings.fact_model
            config.web_model = settings.web_model
            config.api_key_env = _RUNTIME_KEY_ENV
            os.environ[_RUNTIME_KEY_ENV] = key
            return

        profile = load_relay_profile()
        key = load_relay_key()
        if profile is None or not key:
            raise CapabilityProbeError("当前选择中转站，但中转站配置或 Key 不完整。")
        bindings = (
            RoleBinding("semantic", profile.base_url, profile.model, key).normalized(),
            RoleBinding("fact", profile.base_url, profile.fact_model, key).normalized(),
            RoleBinding("web", profile.base_url, profile.web_model, key).normalized(),
        )
        managed = assert_verified_if_managed(
            config_dir=relay_verification_directory(),
            bindings=bindings,
        )
        if not managed:
            raise CapabilityProbeError("中转站配置缺少能力验证记录，请先在 AI 服务设置中测试并保存。")
        config.provider = "openai-compatible"
        config.base_url = profile.base_url
        config.local_model = profile.model
        config.fact_model = profile.fact_model
        config.web_model = profile.web_model
        config.api_key_env = _RUNTIME_KEY_ENV
        os.environ[_RUNTIME_KEY_ENV] = key


def install_ai_settings_modal(window) -> AISettingsModalController:
    existing = getattr(window, "_ai_settings_controller", None)
    if isinstance(existing, AISettingsModalController):
        return existing
    controller = AISettingsModalController(window)
    window._ai_settings_controller = controller
    return controller


__all__ = ["AISettingsContent", "AISettingsModalController", "install_ai_settings_modal"]
