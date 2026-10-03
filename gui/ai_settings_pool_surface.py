from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Signal, Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices
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
    discover_models,
    load_verification_snapshot,
    probe_role,
    save_verification_snapshot,
)
from app.ai_profile_store import (
    AI_SOURCE_QWEN,
    AI_SOURCE_RELAY,
    load_active_ai_source,
    load_qwen_profile,
    relay_verification_directory,
    save_active_ai_source,
)
from app.ai_relay_pool import (
    RelayConnection,
    RelayPoolProfile,
    RelayRoleBinding,
    has_relay_connection_key,
    load_relay_connection_key,
    load_relay_pool,
    save_relay_connection_key,
    save_relay_pool,
)
from app.ai_service_settings import (
    RUNTIME_FACT_BASE_URL_ENV,
    RUNTIME_FACT_KEY_ENV,
    RUNTIME_WEB_BASE_URL_ENV,
    RUNTIME_WEB_KEY_ENV,
    has_ai_service_key,
    load_ai_service_settings,
    save_ai_service_key,
)
from .ai_settings_surface import AISettingsModalController as _BaseAISettingsModalController


_RUNTIME_KEY_ENV = "AI_API_KEY"
_MASKED_KEY = "••••••••••••••••"
_CONNECTION_IDS = ("relay-1", "relay-2", "relay-3")
_CONNECTION_LABELS = {"relay-1": "连接 A", "relay-2": "连接 B", "relay-3": "连接 C"}
_ROLES = ("semantic", "fact", "web")
_ROLE_LABELS = {"semantic": "主语义", "fact": "Fact", "web": "Web"}
_ROLE_REQUIREMENTS = {
    "semantic": "Strict JSON Schema + Vision 真读图",
    "fact": "Strict JSON Schema",
    "web": "Responses API + web_search + 来源 URL",
}

_STYLE = r"""
QWidget#aiPoolSettings { background: transparent; }
QLabel#aiHint { color: rgba(255,255,255,188); font-size: 11px; }
QLabel#aiTitle { color: rgba(255,255,255,242); font-size: 13px; font-weight: 760; }
QLabel#aiMeta { color: rgba(255,255,255,145); font-size: 10px; }
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
QLineEdit#aiInput:read-only { color: rgba(255,255,255,178); background-color: rgba(10,20,32,56); }
"""


@dataclass(slots=True)
class _ConnectionDraft:
    enabled: bool = False
    base_url: str = ""
    key_draft: str = ""
    key_editing: bool = False
    key_configured: bool = False


class AISettingsContent(QWidget):
    """Stable Qwen profile plus a relay connection pool with per-role bindings."""

    catalog_finished = Signal(object)
    probe_finished = Signal(object)
    probe_progress = Signal(str, str)
    probe_role_finished = Signal(object)
    activity_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiPoolSettings")
        self.setStyleSheet(_STYLE)
        self._loading = False
        self._busy = False
        self._qwen_key_configured = False
        self._qwen_key_editing = False
        self._loaded_pool: RelayPoolProfile | None = None
        self._relay_dirty = False
        self._drafts = {cid: _ConnectionDraft(enabled=(cid == "relay-1")) for cid in _CONNECTION_IDS}
        self._catalog_available: dict[str, bool | None] = {cid: None for cid in _CONNECTION_IDS}
        self._catalogs: dict[str, tuple[str, ...] | None] = {cid: None for cid in _CONNECTION_IDS}
        self._catalog_status: dict[str, str] = {cid: "尚未探测模型。" for cid in _CONNECTION_IDS}
        self._verified_reports: dict[str, RoleCapabilityReport] = {}
        self._verified_signatures: dict[str, str] = {}

        self.catalog_finished.connect(self._catalog_done)
        self.probe_finished.connect(self._probe_done)
        self.probe_progress.connect(self._stage_progress)
        self.probe_role_finished.connect(self._role_finished)
        self._activity_kind = ""
        self._activity_frame = 0
        self._completed_roles = 0
        self._active_role = ""
        self._active_stage = ""
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(250)
        self._activity_timer.timeout.connect(self._activity_tick)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        intro = QLabel("官方 Qwen 与中转站完全隔离；中转站内部每个角色可以复用同一条连接，也可以绑定不同 URL / Key / 模型。")
        intro.setObjectName("aiHint")
        intro.setWordWrap(True)
        root.addWidget(intro)

        source_card = QFrame()
        source_card.setObjectName("cardDetailSection")
        source_form = QGridLayout(source_card)
        source_form.setContentsMargins(15, 13, 15, 13)
        source_form.setColumnStretch(1, 1)
        self.source = QComboBox()
        self.source.setObjectName("modalCombo")
        self.source.addItem("官方 Qwen（稳定配置）", AI_SOURCE_QWEN)
        self.source.addItem("中转站连接池", AI_SOURCE_RELAY)
        self.source.currentIndexChanged.connect(self._source_changed)
        self._field(source_form, 0, "当前模型来源", self.source)
        root.addWidget(source_card)

        self.qwen_card = self._build_qwen_card()
        self.relay_card = self._build_relay_card()
        root.addWidget(self.qwen_card)
        root.addWidget(self.relay_card)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.save_button = QPushButton("保存并使用当前来源")
        self.save_button.setObjectName("modalPrimaryButton")
        self.save_button.clicked.connect(self._save_current)
        actions.addWidget(self.save_button)
        root.addLayout(actions)
        self.reload()

    @staticmethod
    def _input(placeholder: str = "", *, read_only: bool = False) -> QLineEdit:
        editor = QLineEdit()
        editor.setObjectName("aiInput")
        editor.setPlaceholderText(placeholder)
        editor.setReadOnly(read_only)
        return editor

    @staticmethod
    def _status(text: str = "") -> QLabel:
        label = QLabel(text)
        label.setObjectName("aiMeta")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _field(form: QGridLayout, row: int, label: str, widget: QWidget, column: int = 1) -> None:
        title = QLabel(label)
        title.setObjectName("modalFieldLabel")
        form.addWidget(title, row, 0, Qt.AlignmentFlag.AlignVCenter)
        form.addWidget(widget, row, column)

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
        title.setObjectName("aiTitle")
        form.addWidget(title, 0, 0, 1, 2)
        note = QLabel("沿用现有 DashScope/Qwen 配置。中转站的任何 URL、Key、模型修改都不会覆盖这里。")
        note.setObjectName("aiHint")
        note.setWordWrap(True)
        form.addWidget(note, 1, 0, 1, 2)
        self.qwen_base = self._input(read_only=True)
        self.qwen_semantic = self._input(read_only=True)
        self.qwen_fact = self._input(read_only=True)
        self.qwen_web = self._input(read_only=True)
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

        title = QLabel("中转站 · 连接池")
        title.setObjectName("aiTitle")
        layout.addWidget(title)
        hint = QLabel("先配置并探测连接，再在下方给主语义 / Fact / Web 分别选择连接和模型。连接 A/B/C 的 Key 独立保存。")
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

        self.connection_slot = QComboBox()
        self.connection_slot.setObjectName("modalCombo")
        for cid in _CONNECTION_IDS:
            self.connection_slot.addItem(_CONNECTION_LABELS[cid], cid)
        self.connection_slot.currentIndexChanged.connect(self._connection_slot_changed)
        self.connection_enabled = QCheckBox("启用此连接")
        self.connection_enabled.toggled.connect(self._connection_enabled_changed)
        self.connection_base = self._input("https://relay.example/v1")
        self.connection_base.textChanged.connect(self._connection_edited)
        self.connection_key = self._input("中转站 API Key")
        self.connection_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.connection_key.textChanged.connect(self._connection_key_text_changed)
        self.connection_key_action = QPushButton("显示")
        self.connection_key_action.setObjectName("modalPrimaryButton")
        self.connection_key_action.setMaximumWidth(72)
        self.connection_key_action.clicked.connect(self._connection_key_action)
        self.catalog_button = QPushButton("探测当前连接模型")
        self.catalog_button.setObjectName("modalPrimaryButton")
        self.catalog_button.clicked.connect(self._start_catalog)
        self.catalog_status = self._status()
        self._field(form, 0, "连接", self.connection_slot)
        self._field(form, 1, "状态", self.connection_enabled)
        self._field(form, 2, "Base URL", self.connection_base)
        self._field(form, 3, "API Key", self._key_row(self.connection_key, self.connection_key_action))
        form.addWidget(self.catalog_button, 4, 1)
        form.addWidget(self.catalog_status, 5, 1)
        layout.addWidget(connection)

        roles = QFrame()
        roles.setObjectName("cardDetailSection")
        role_form = QGridLayout(roles)
        role_form.setContentsMargins(12, 12, 12, 12)
        role_form.setHorizontalSpacing(10)
        role_form.setVerticalSpacing(9)
        role_form.setColumnStretch(1, 1)
        role_form.setColumnStretch(2, 2)
        role_form.addWidget(QLabel("角色"), 0, 0)
        role_form.addWidget(QLabel("连接"), 0, 1)
        role_form.addWidget(QLabel("模型"), 0, 2)
        self.role_connections: dict[str, QComboBox] = {}
        self.role_models: dict[str, QComboBox] = {}
        self.role_manual: dict[str, QLineEdit] = {}
        self.role_manual_labels: dict[str, QLabel] = {}
        for row, role in enumerate(_ROLES, start=1):
            name = QLabel(_ROLE_LABELS[role])
            name.setObjectName("modalFieldLabel")
            connection_combo = QComboBox()
            connection_combo.setObjectName("modalCombo")
            connection_combo.currentIndexChanged.connect(lambda _i, r=role: self._role_connection_changed(r))
            model_combo = QComboBox()
            model_combo.setObjectName("modalCombo")
            model_combo.currentIndexChanged.connect(lambda _i, r=role: self._role_model_changed(r))
            self.role_connections[role] = connection_combo
            self.role_models[role] = model_combo
            role_form.addWidget(name, row, 0)
            role_form.addWidget(connection_combo, row, 1)
            role_form.addWidget(model_combo, row, 2)
            manual_label = QLabel(f"{_ROLE_LABELS[role]} 手动模型 ID")
            manual_label.setObjectName("modalFieldLabel")
            manual = self._input("仅在该连接不支持 /models 时使用")
            manual.textChanged.connect(lambda _text, r=role: self._role_model_changed(r))
            manual_label.hide()
            manual.hide()
            self.role_manual[role] = manual
            self.role_manual_labels[role] = manual_label
            role_form.addWidget(manual_label, row + 3, 0, 1, 2)
            role_form.addWidget(manual, row + 3, 2)
        layout.addWidget(roles)

        capability = QFrame()
        capability.setObjectName("cardDetailSection")
        cap_form = QGridLayout(capability)
        cap_form.setContentsMargins(12, 12, 12, 12)
        cap_form.setHorizontalSpacing(14)
        cap_form.setVerticalSpacing(8)
        cap_form.setColumnStretch(1, 1)
        self.capability_status: dict[str, QLabel] = {}
        for row, role in enumerate(_ROLES):
            name = QLabel(_ROLE_LABELS[role])
            name.setObjectName("modalFieldLabel")
            status = self._status("尚未验证 · " + _ROLE_REQUIREMENTS[role])
            self.capability_status[role] = status
            cap_form.addWidget(name, row, 0)
            cap_form.addWidget(status, row, 1)
        self.verify_button = QPushButton("测试三个角色的实际能力")
        self.verify_button.setObjectName("modalPrimaryButton")
        self.verify_button.clicked.connect(self._start_probe)
        cap_form.addWidget(self.verify_button, 3, 1)
        self.probe_summary = self._status("")
        cap_form.addWidget(self.probe_summary, 4, 1)
        self.probe_log_button = QPushButton("打开诊断日志")
        self.probe_log_button.setObjectName("modalSecondaryButton")
        self.probe_log_button.setEnabled(False)
        self.probe_log_button.clicked.connect(self._open_probe_logs)
        cap_form.addWidget(self.probe_log_button, 5, 1)
        self._probe_log_path = ""
        layout.addWidget(capability)
        return card

    # --------------------------------------------------------------- loading
    def reload(self) -> None:
        self._loading = True
        try:
            try:
                qwen, _ = load_qwen_profile()
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

            self._loaded_pool = load_relay_pool()
            self._drafts = {cid: _ConnectionDraft(enabled=(cid == "relay-1")) for cid in _CONNECTION_IDS}
            if self._loaded_pool is not None:
                for item in self._loaded_pool.connections:
                    self._drafts[item.connection_id].enabled = True
                    self._drafts[item.connection_id].base_url = item.base_url
            for cid in _CONNECTION_IDS:
                self._drafts[cid].key_configured = has_relay_connection_key(cid)
                self._drafts[cid].key_editing = not self._drafts[cid].key_configured
            self._catalog_available = {cid: None for cid in _CONNECTION_IDS}
            self._catalogs = {cid: None for cid in _CONNECTION_IDS}
            self._catalog_status = {cid: "尚未探测模型。" for cid in _CONNECTION_IDS}
            self._relay_dirty = False
            self._refresh_role_connection_choices()
            self._load_saved_role_values()
            self._load_verification()
            self.connection_slot.setCurrentIndex(0)
            self._load_connection_editor("relay-1")

            active = load_active_ai_source()
            index = self.source.findData(active)
            self.source.blockSignals(True)
            self.source.setCurrentIndex(index if index >= 0 else 0)
            self.source.blockSignals(False)
        finally:
            self._loading = False
        self._source_changed()

    def _load_saved_role_values(self) -> None:
        if self._loaded_pool is None:
            return
        for role in _ROLES:
            binding = self._loaded_pool.binding_for(role)
            combo = self.role_connections[role]
            idx = combo.findData(binding.connection_id)
            combo.blockSignals(True)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
            model = self.role_models[role]
            model.blockSignals(True)
            model.clear()
            model.addItem(binding.model)
            model.setCurrentIndex(0)
            model.blockSignals(False)

    def _load_verification(self) -> None:
        self._verified_reports.clear()
        self._verified_signatures.clear()
        try:
            snapshot = load_verification_snapshot(config_dir=relay_verification_directory())
            bindings = self._bindings_from_ui(require_catalog=False)
        except Exception:
            snapshot = None
            bindings = ()
        if snapshot is None or not bindings:
            for role in _ROLES:
                self._set_capability(role, "warn", "尚未验证 · " + _ROLE_REQUIREMENTS[role])
            return
        records = snapshot.by_role()
        signatures = {item.role: binding_signature(item) for item in bindings}
        if any(
            role not in records or not records[role].passed or records[role].binding_signature != signatures[role]
            for role in _ROLES
        ):
            for role in _ROLES:
                self._set_capability(role, "warn", "已保存验证与当前连接/Key/模型不匹配，请重新测试。")
            return
        self._verified_signatures = signatures
        self._verified_reports = {
            role: RoleCapabilityReport(
                role=role,
                base_url=records[role].base_url,
                model=records[role].model,
                passed=True,
                checks=records[role].checks,
            )
            for role in _ROLES
        }
        for role in _ROLES:
            self._set_capability(role, "pass", f"✓ 已验证 · {records[role].model}")

    # ------------------------------------------------------------ connections
    def _current_connection_id(self) -> str:
        return str(self.connection_slot.currentData() or "relay-1")

    def _stash_connection_editor(self) -> None:
        cid = self._current_connection_id()
        draft = self._drafts[cid]
        draft.base_url = self.connection_base.text().strip()
        if draft.key_editing:
            value = self.connection_key.text().strip()
            if value and value != _MASKED_KEY:
                draft.key_draft = value

    def _load_connection_editor(self, cid: str) -> None:
        draft = self._drafts[cid]
        self.connection_enabled.blockSignals(True)
        self.connection_enabled.setChecked(draft.enabled)
        self.connection_enabled.setEnabled(cid != "relay-1")
        self.connection_enabled.blockSignals(False)
        self.connection_base.blockSignals(True)
        self.connection_base.setText(draft.base_url)
        self.connection_base.blockSignals(False)
        self.connection_key.blockSignals(True)
        if draft.key_editing:
            self.connection_key.setReadOnly(False)
            self.connection_key.setText(draft.key_draft)
            self.connection_key_action.setText("显示")
        elif draft.key_configured:
            self.connection_key.setReadOnly(True)
            self.connection_key.setText(_MASKED_KEY)
            self.connection_key_action.setText("更改")
        else:
            self.connection_key.setReadOnly(False)
            self.connection_key.clear()
            self.connection_key_action.setText("显示")
        self.connection_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.connection_key.blockSignals(False)
        enabled = draft.enabled
        self.connection_base.setEnabled(enabled)
        self.connection_key.setEnabled(enabled)
        self.connection_key_action.setEnabled(enabled)
        self.catalog_button.setEnabled(enabled and not self._busy)
        self.catalog_status.setText(self._catalog_status[cid])

    def _connection_slot_changed(self, *_args: object) -> None:
        if self._loading:
            return
        # The previous editor is already reflected through textChanged; load selected slot.
        self._load_connection_editor(self._current_connection_id())

    def _connection_enabled_changed(self, checked: bool) -> None:
        if self._loading:
            return
        cid = self._current_connection_id()
        if cid == "relay-1" and not checked:
            return
        self._drafts[cid].enabled = bool(checked)
        self._relay_dirty = True
        self._invalidate_verification("连接池已变化")
        self._refresh_role_connection_choices()
        self._load_connection_editor(cid)

    def _connection_edited(self, *_args: object) -> None:
        if self._loading or self._busy:
            return
        cid = self._current_connection_id()
        self._drafts[cid].base_url = self.connection_base.text().strip()
        self._catalog_available[cid] = None
        self._catalogs[cid] = None
        self._catalog_status[cid] = "连接已变化，请重新探测模型。"
        self.catalog_status.setText(self._catalog_status[cid])
        self._relay_dirty = True
        self._invalidate_verification("中转 URL 已变化")

    def _connection_key_text_changed(self, *_args: object) -> None:
        if self._loading or self._busy:
            return
        cid = self._current_connection_id()
        draft = self._drafts[cid]
        if not draft.key_editing:
            return
        value = self.connection_key.text().strip()
        if value and value != _MASKED_KEY:
            draft.key_draft = value
            self._catalog_available[cid] = None
            self._catalogs[cid] = None
            self._catalog_status[cid] = "Key 已变化，请重新探测模型。"
            self.catalog_status.setText(self._catalog_status[cid])
            self._relay_dirty = True
            self._invalidate_verification("中转 Key 已变化")

    def _connection_key_action(self) -> None:
        cid = self._current_connection_id()
        draft = self._drafts[cid]
        if draft.key_configured and not draft.key_editing:
            draft.key_editing = True
            self.connection_key.setReadOnly(False)
            self.connection_key.clear()
            self.connection_key.setPlaceholderText("输入新的 API Key")
            self.connection_key_action.setText("显示")
            return
        visible = self.connection_key.echoMode() == QLineEdit.EchoMode.Password
        self.connection_key.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.connection_key_action.setText("隐藏" if visible else "显示")

    def _effective_key(self, cid: str) -> str:
        draft = self._drafts[cid]
        if draft.key_editing and draft.key_draft:
            return draft.key_draft
        return load_relay_connection_key(cid)

    def _start_catalog(self) -> None:
        if self._busy:
            return
        cid = self._current_connection_id()
        draft = self._drafts[cid]
        if not draft.enabled:
            return
        base = self.connection_base.text().strip()
        key = self._effective_key(cid)
        if not key:
            QMessageBox.warning(self, "缺少 API Key", f"请先填写{_CONNECTION_LABELS[cid]}的 API Key。")
            return
        try:
            RoleBinding("fact", base, "catalog-probe", key).normalized()
        except Exception as exc:
            QMessageBox.warning(self, "连接无效", str(exc))
            return
        self._set_busy(True)
        self._begin_activity("catalog")
        expected = base.rstrip("/")
        def worker() -> None:
            result = discover_models(base_url=base, api_key=key)
            self.catalog_finished.emit((cid, expected, result))
        threading.Thread(target=worker, name=f"relay-catalog-{cid}", daemon=True).start()

    def _catalog_done(self, payload: object) -> None:
        self._set_busy(False)
        try:
            cid, expected, result = payload  # type: ignore[misc]
            assert isinstance(result, ModelCatalogResult)
        except Exception:
            return
        if self._drafts[cid].base_url.rstrip("/") != expected:
            return
        self._catalog_available[cid] = bool(result.catalog_available)
        self._catalogs[cid] = tuple(result.models) if result.catalog_available else None
        if result.catalog_available and result.models:
            text = f"✓ 探测成功 · 发现 {len(result.models)} 个可用模型。"
        elif result.catalog_available:
            text = "连接成功，但 /models 没有返回模型。"
        else:
            text = "该连接不提供 /models；绑定到它的角色可手动填写模型 ID。\n" + result.error
        text += f" · 耗时 {getattr(result, 'elapsed_seconds', 0):.1f} 秒"
        if getattr(result, "log_path", ""):
            self._probe_log_path = result.log_path
            self.probe_log_button.setEnabled(True)
        self._catalog_status[cid] = text
        if cid == self._current_connection_id():
            self.catalog_status.setText(text)
        for role in _ROLES:
            if self.role_connections[role].currentData() == cid:
                self._refresh_role_model(role)
        self._relay_dirty = True
        self._invalidate_verification("模型目录已刷新")

    # --------------------------------------------------------------- roles
    def _enabled_connection_ids(self) -> list[str]:
        return [cid for cid in _CONNECTION_IDS if self._drafts[cid].enabled]

    def _refresh_role_connection_choices(self) -> None:
        enabled = self._enabled_connection_ids()
        for role in _ROLES:
            combo = self.role_connections[role]
            previous = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for cid in enabled:
                combo.addItem(_CONNECTION_LABELS[cid], cid)
            index = combo.findData(previous)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)
            self._refresh_role_model(role)

    def _selected_role_model(self, role: str) -> str:
        cid = str(self.role_connections[role].currentData() or "")
        if self._catalog_available.get(cid) is False:
            manual = self.role_manual[role].text().strip()
            if manual:
                return manual
        return self.role_models[role].currentText().strip()

    def _refresh_role_model(self, role: str) -> None:
        cid = str(self.role_connections[role].currentData() or "")
        combo = self.role_models[role]
        previous = combo.currentText().strip()
        catalog_state = self._catalog_available.get(cid)
        catalog = self._catalogs.get(cid)
        combo.blockSignals(True)
        if catalog_state is True:
            combo.clear()
            combo.addItems(catalog or ())
            idx = combo.findText(previous)
            combo.setCurrentIndex(idx if idx >= 0 else (0 if combo.count() else -1))
        elif combo.count() == 0 and previous:
            combo.addItem(previous)
        combo.blockSignals(False)
        manual_visible = catalog_state is False
        self.role_manual[role].setVisible(manual_visible)
        self.role_manual_labels[role].setVisible(manual_visible)

    def _role_connection_changed(self, role: str) -> None:
        if self._loading or self._busy:
            return
        self._refresh_role_model(role)
        self._relay_dirty = True
        self._invalidate_verification(f"{_ROLE_LABELS[role]}连接已变化")

    def _role_model_changed(self, role: str) -> None:
        if self._loading or self._busy:
            return
        self._relay_dirty = True
        self._invalidate_verification(f"{_ROLE_LABELS[role]}模型已变化")

    def _bindings_from_ui(self, *, require_catalog: bool) -> tuple[RoleBinding, RoleBinding, RoleBinding]:
        result: list[RoleBinding] = []
        for role in _ROLES:
            cid = str(self.role_connections[role].currentData() or "")
            if cid not in self._enabled_connection_ids():
                raise CapabilityProbeError(f"{_ROLE_LABELS[role]}没有选择可用连接。")
            if require_catalog and self._catalog_available.get(cid) is None:
                raise CapabilityProbeError(f"请先探测{_CONNECTION_LABELS[cid]}，再测试{_ROLE_LABELS[role]}能力。")
            key = self._effective_key(cid)
            base = self._drafts[cid].base_url
            model = self._selected_role_model(role)
            binding = RoleBinding(role, base, model, key).normalized()
            catalog = self._catalogs.get(cid)
            if self._catalog_available.get(cid) is True and catalog is not None and model not in catalog:
                raise CapabilityProbeError(f"{_ROLE_LABELS[role]}模型不在{_CONNECTION_LABELS[cid]}返回的 /models 中。")
            result.append(binding)
        return tuple(result)  # type: ignore[return-value]

    # ------------------------------------------------------------- capability
    def _begin_activity(self, kind: str) -> None:
        self._activity_kind = kind
        self._activity_started = time.monotonic()
        self._stage_started = self._activity_started
        self._activity_frame = 0
        self._completed_roles = 0
        self._active_role = ""
        self._active_stage = "准备连接"
        self._activity_timer.start()
        self._activity_tick()

    def _activity_tick(self) -> None:
        if not self._busy or not self._activity_kind:
            return
        spinner = ("◐", "◓", "◑", "◒")[self._activity_frame % 4]
        self._activity_frame += 1
        elapsed = int(time.monotonic() - self._activity_started)
        if self._activity_kind == "catalog":
            self.catalog_button.setText(f"{spinner} 正在探测 · {elapsed} 秒")
            self.catalog_status.setText(f"正在获取模型列表 · 已等待 {elapsed} 秒")
        else:
            self.verify_button.setText(f"{spinner} 测试中 · 已完成 {self._completed_roles}/3 个角色")
            stage_elapsed = int(time.monotonic() - self._stage_started)
            self.probe_summary.setText(f"已用 {elapsed} 秒 · 已完成 {self._completed_roles}/3 个角色 · 每次请求最多等待 120 秒")
            if self._active_role:
                self.capability_status[self._active_role].setText(
                    f"{spinner} {self._active_stage} · 已等待 {stage_elapsed} 秒")
        self.activity_changed.emit()

    def _stage_progress(self, role: str, stage: str) -> None:
        self._active_role = role
        self._active_stage = {"client_init": "连接中", "strict_json_schema": "验证结构化输出",
                              "vision": "验证图片识别", "web_search": "验证联网搜索与来源",
                              "vision_retry": "图片返回格式异常，正在纠正重试",
                              "strict_json_schema_retry": "返回格式异常，正在纠正重试"}.get(stage, stage)
        self._stage_started = time.monotonic()
        self._activity_tick()

    def _role_finished(self, report: RoleCapabilityReport) -> None:
        self._active_role = ""
        self._completed_roles += 1
        if report.passed:
            self._set_capability(report.role, "pass", "✓ 测试通过")
        else:
            self._set_capability(report.role, "fail", "✕ 测试失败 · " + report.error)
        self._activity_tick()

    def _open_probe_logs(self) -> None:
        if self._probe_log_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._probe_log_path).parent)))

    def _start_probe(self) -> None:
        if self._busy:
            return
        try:
            bindings = self._bindings_from_ui(require_catalog=True)
        except Exception as exc:
            QMessageBox.warning(self, "无法开始能力测试", str(exc))
            return
        signatures = {item.role: binding_signature(item) for item in bindings}
        self._set_busy(True)
        self.probe_log_button.setEnabled(False)
        for role in _ROLES:
            self._set_capability(role, "warn", "等待测试")
        self._begin_activity("probe")
        def worker() -> None:
            reports: list[RoleCapabilityReport] = []
            for binding in bindings:
                report = probe_role(binding, timeout=120.0,
                                    progress_callback=lambda stage, role=binding.role: self.probe_progress.emit(role, stage))
                reports.append(report)
                self.probe_role_finished.emit(report)
            self.probe_finished.emit((signatures, tuple(reports)))
        threading.Thread(target=worker, name="relay-pool-capability", daemon=True).start()

    def _probe_done(self, payload: object) -> None:
        self._set_busy(False)
        try:
            signatures, reports = payload  # type: ignore[misc]
            current = self._bindings_from_ui(require_catalog=True)
            current_signatures = {item.role: binding_signature(item) for item in current}
        except Exception as exc:
            self._invalidate_verification("测试期间配置已变化")
            QMessageBox.warning(self, "能力测试结果已失效", str(exc))
            return
        if current_signatures != signatures:
            self._invalidate_verification("测试期间配置已变化")
            return
        report_by_role = {item.role: item for item in reports}
        failed = False
        for role in _ROLES:
            report = report_by_role.get(role)
            if report is None:
                failed = True
                self._set_capability(role, "fail", "前置角色失败，本角色未继续测试。")
            elif report.passed:
                checks = " · ".join(item.name for item in report.checks if item.passed)
                self._set_capability(role, "pass", "✓ 通过 · " + checks)
            else:
                failed = True
                stage = getattr(report, "failed_stage", "")
                stage = {"client_init": "连接初始化", "strict_json_schema": "严格 JSON 测试",
                         "vision": "图片识别测试", "web_search": "联网搜索测试"}.get(stage, stage)
                self._set_capability(role, "fail", "✕ 失败 · " + (stage + " · " if stage else "") + (report.error or "能力不匹配"))
            if report is not None:
                self.capability_status[role].setToolTip("")
        if failed:
            self._verified_signatures.clear()
            self._verified_reports = report_by_role
            paths = [getattr(item, "log_path", "") for item in reports if getattr(item, "log_path", "")]
            if paths:
                self._probe_log_path = paths[-1]
                self.probe_log_button.setEnabled(True)
                self.probe_summary.setText("测试未通过，诊断日志已保存。")
            else:
                self.probe_summary.setText("测试未通过，诊断日志写入失败。")
            return
        self._verified_signatures = dict(signatures)
        self._verified_reports = report_by_role
        self.probe_summary.setText("三个角色均通过，可以保存并使用当前中转站组合。")
        paths = [getattr(item, "log_path", "") for item in reports if getattr(item, "log_path", "")]
        if paths:
            self._probe_log_path = paths[-1]
            self.probe_log_button.setEnabled(True)

    def _set_capability(self, role: str, state: str, text: str) -> None:
        label = self.capability_status[role]
        label.setText(text)
        label.setObjectName("aiPass" if state == "pass" else "aiFail" if state == "fail" else "aiWarn")
        label.style().unpolish(label)
        label.style().polish(label)

    def _invalidate_verification(self, reason: str) -> None:
        self._verified_signatures.clear()
        self._verified_reports.clear()
        for role in _ROLES:
            self._set_capability(role, "warn", "需要重新验证 · " + reason)

    # --------------------------------------------------------------- save
    def _source_changed(self, *_args: object) -> None:
        source = self.source.currentData()
        self.qwen_card.setVisible(source == AI_SOURCE_QWEN)
        self.relay_card.setVisible(source == AI_SOURCE_RELAY)
        self.save_button.setText("使用官方 Qwen" if source == AI_SOURCE_QWEN else "保存并使用中转站")

    def _qwen_key_action(self) -> None:
        if self._qwen_key_configured and not self._qwen_key_editing:
            self._qwen_key_editing = True
            self.qwen_key.setReadOnly(False)
            self.qwen_key.clear()
            self.qwen_key_action.setText("显示")
            return
        visible = self.qwen_key.echoMode() == QLineEdit.EchoMode.Password
        self.qwen_key.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.qwen_key_action.setText("隐藏" if visible else "显示")

    def _pool_from_ui(self) -> RelayPoolProfile:
        connections = tuple(
            RelayConnection(cid, _CONNECTION_LABELS[cid], self._drafts[cid].base_url)
            for cid in self._enabled_connection_ids()
        )
        bindings = {
            role: RelayRoleBinding(
                str(self.role_connections[role].currentData() or ""),
                self._selected_role_model(role),
            )
            for role in _ROLES
        }
        return RelayPoolProfile(
            connections=connections,
            semantic=bindings["semantic"],
            fact=bindings["fact"],
            web=bindings["web"],
        ).validated()

    def _save_current(self) -> None:
        if self.source.currentData() == AI_SOURCE_QWEN:
            if self._qwen_key_editing:
                value = self.qwen_key.text().strip()
                if value and value != _MASKED_KEY:
                    save_ai_service_key(value)
            if not has_ai_service_key():
                QMessageBox.warning(self, "Qwen Key 未配置", "请先配置官方 Qwen API Key。")
                return
            save_active_ai_source(AI_SOURCE_QWEN)
            self.reload()
            QMessageBox.information(self, "已切换", "已使用官方 Qwen；中转站配置保持不变。")
            return

        try:
            pool = self._pool_from_ui()
            bindings = self._bindings_from_ui(require_catalog=self._relay_dirty)
        except Exception as exc:
            QMessageBox.warning(self, "中转配置不完整", str(exc))
            return
        signatures = {item.role: binding_signature(item) for item in bindings}
        if self._relay_dirty and signatures != self._verified_signatures:
            QMessageBox.warning(self, "请先测试能力", "连接、Key 或角色模型已经变化。请先探测相关连接并通过三个角色的真实能力测试。")
            return
        if not self._relay_dirty:
            try:
                managed = assert_verified_if_managed(config_dir=relay_verification_directory(), bindings=bindings)
            except Exception as exc:
                QMessageBox.warning(self, "验证已失效", str(exc))
                return
            if not managed:
                QMessageBox.warning(self, "尚未验证", "这套中转组合没有能力验证记录，请先测试。")
                return
        try:
            for cid in self._enabled_connection_ids():
                draft = self._drafts[cid]
                if draft.key_editing and draft.key_draft:
                    save_relay_connection_key(cid, draft.key_draft)
                if not has_relay_connection_key(cid):
                    raise CapabilityProbeError(f"{_CONNECTION_LABELS[cid]}缺少 API Key。")
            saved = save_relay_pool(pool)
            final_bindings: list[RoleBinding] = []
            for role in _ROLES:
                choice = saved.binding_for(role)
                conn = saved.connection(choice.connection_id)
                final_bindings.append(RoleBinding(role, conn.base_url, choice.model, load_relay_connection_key(choice.connection_id)).normalized())
            final_tuple = tuple(final_bindings)
            final_signatures = {item.role: binding_signature(item) for item in final_tuple}
            if self._relay_dirty:
                if final_signatures != signatures:
                    raise CapabilityProbeError("保存后的连接签名与刚才验证的组合不一致。")
                save_verification_snapshot(
                    config_dir=relay_verification_directory(),
                    bindings=final_tuple,
                    reports=tuple(self._verified_reports[role] for role in _ROLES),
                )
            else:
                assert_verified_if_managed(config_dir=relay_verification_directory(), bindings=final_tuple)
            save_active_ai_source(AI_SOURCE_RELAY)
        except Exception as exc:
            QMessageBox.critical(self, "中转站配置无法保存", str(exc))
            return
        self.reload()
        QMessageBox.information(self, "已切换", "已使用中转站连接池；官方 Qwen 配置完全未改动。")

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        if not busy:
            self._activity_timer.stop()
            self._activity_kind = ""
            self.catalog_button.setText("探测当前连接模型")
            self.verify_button.setText("测试三个角色的实际能力")
            self.activity_changed.emit()
        self.catalog_button.setEnabled(not busy and self._drafts[self._current_connection_id()].enabled)
        self.verify_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy)


class AISettingsModalController(_BaseAISettingsModalController):
    """Runtime binding for stable Qwen or a per-role relay connection pool."""

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
            quick = getattr(self.window, "_static_qml_view_controller", None)
            refresh = getattr(getattr(quick, "bridge", None), "schedule_refresh", None)
            if callable(refresh):
                panel.activity_changed.connect(refresh)
            body_layout.addWidget(panel, 1)
        open_custom(title="AI 服务设置", eyebrow="SETTINGS · AI PROFILES", populate=populate, ratio=(0.72, 0.84))

    @staticmethod
    def _clear_role_runtime() -> None:
        for name in (RUNTIME_FACT_BASE_URL_ENV, RUNTIME_FACT_KEY_ENV, RUNTIME_WEB_BASE_URL_ENV, RUNTIME_WEB_KEY_ENV):
            os.environ.pop(name, None)

    def _apply_runtime(self, config) -> None:
        self._clear_role_runtime()
        source = load_active_ai_source()
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

        pool = load_relay_pool()
        if pool is None:
            raise CapabilityProbeError("当前选择中转站，但连接池尚未配置。")
        runtime_bindings: dict[str, RoleBinding] = {}
        for role in _ROLES:
            choice = pool.binding_for(role)
            conn = pool.connection(choice.connection_id)
            key = load_relay_connection_key(choice.connection_id)
            if not key:
                raise CapabilityProbeError(f"{_CONNECTION_LABELS[choice.connection_id]}缺少 API Key。")
            runtime_bindings[role] = RoleBinding(role, conn.base_url, choice.model, key).normalized()
        ordered = tuple(runtime_bindings[role] for role in _ROLES)
        managed = assert_verified_if_managed(config_dir=relay_verification_directory(), bindings=ordered)
        if not managed:
            raise CapabilityProbeError("中转站连接池缺少能力验证记录，请先在设置中完成测试。")

        semantic = runtime_bindings["semantic"]
        fact = runtime_bindings["fact"]
        web = runtime_bindings["web"]
        config.provider = "openai-compatible"
        config.base_url = semantic.base_url
        config.local_model = semantic.model
        config.fact_model = fact.model
        config.web_model = web.model
        config.api_key_env = _RUNTIME_KEY_ENV
        os.environ[_RUNTIME_KEY_ENV] = semantic.api_key
        if fact.base_url != semantic.base_url or fact.api_key != semantic.api_key:
            os.environ[RUNTIME_FACT_BASE_URL_ENV] = fact.base_url
            os.environ[RUNTIME_FACT_KEY_ENV] = fact.api_key
        if web.base_url != semantic.base_url or web.api_key != semantic.api_key:
            os.environ[RUNTIME_WEB_BASE_URL_ENV] = web.base_url
            os.environ[RUNTIME_WEB_KEY_ENV] = web.api_key


def install_ai_settings_modal(window) -> AISettingsModalController:
    existing = getattr(window, "_ai_settings_controller", None)
    if isinstance(existing, AISettingsModalController):
        return existing
    controller = AISettingsModalController(window)
    window._ai_settings_controller = controller
    return controller


__all__ = ["AISettingsContent", "AISettingsModalController", "install_ai_settings_modal"]
