from __future__ import annotations

import os
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
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

from app.ai_service_settings import (
    AIServiceSettings,
    RUNTIME_FACT_BASE_URL_ENV,
    RUNTIME_FACT_KEY_ENV,
    RUNTIME_WEB_BASE_URL_ENV,
    RUNTIME_WEB_KEY_ENV,
    clear_ai_service_key,
    has_ai_service_key,
    has_ai_service_role_key,
    load_ai_service_role_key,
    load_ai_service_settings,
    resolved_ai_runtime,
    save_ai_service_role_key,
    save_ai_service_settings,
)
from app.image_optimization_gate import (
    image_optimization_enabled,
    set_image_optimization_enabled,
)


_RUNTIME_KEY_ENV = "AI_API_KEY"
_MASKED_KEY = "••••••••••••••••"
_IMAGE_OPTIMIZATION_GUI_UNLOCKED = False

_CONTENT_STYLE = r"""
QWidget#aiSettingsContent { background: transparent; }
QLabel#aiSettingsHint { color: rgba(255,255,255,196); font-size: 11px; }
QLabel#aiSettingsProvider { color: rgba(255,255,255,238); font-size: 12px; font-weight: 720; }
QLabel#imageOptimizationState { color: rgba(255,255,255,138); font-size: 11px; }
QLabel#aiRoleStatus { color: rgba(255,255,255,138); font-size: 10px; }
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
QLineEdit#aiSettingsInput {
    min-height: 39px;
    padding: 0 12px;
    color: rgba(255,255,255,238);
    background-color: rgba(10,20,32,88);
    border: 1px solid rgba(255,255,255,24);
    border-radius: 8px;
    selection-background-color: rgba(137,190,226,118);
}
QLineEdit#aiSettingsInput:hover {
    background-color: rgba(13,25,39,104);
    border-color: rgba(255,255,255,34);
}
QLineEdit#aiSettingsInput:focus {
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


class AISettingsContent(QWidget):
    """AI configuration body rendered inside the canonical detail modal."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiSettingsContent")
        self.setStyleSheet(_CONTENT_STYLE)
        self._key_configured = False
        self._editing_key = False
        self._role_key_configured = {"fact": False, "web": False}
        self._role_key_editing = {"fact": False, "web": False}
        self._role_key_inputs: dict[str, QLineEdit] = {}
        self._role_key_actions: dict[str, QPushButton] = {}
        self._role_key_status: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        hint = QLabel(
            "使用你自己的 OpenAI-compatible API。API Key 不写入源码、命令参数或运行日志；"
            "Windows 正式版使用当前 Windows 用户的 DPAPI 加密保存。"
        )
        hint.setObjectName("aiSettingsHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        card = QFrame()
        card.setObjectName("cardDetailSection")
        form = QGridLayout(card)
        form.setContentsMargins(15, 14, 15, 15)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)

        provider = QLabel("OpenAI Compatible")
        provider.setObjectName("aiSettingsProvider")
        provider.setToolTip("当前生产 Resolver 使用 OpenAI-compatible 协议。")

        self.base_url = self._input("https://.../compatible-mode/v1")
        self.model = self._input("主模型，例如 qwen3.7-plus")
        self.fact_model = self._input("事实提取模型")
        self.web_model = self._input("Web 搜索模型")
        self.api_key = self._input("输入你自己的 API Key")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)

        self.image_optimization = QCheckBox("开启")
        self.image_optimization.setObjectName("imageOptimizationSwitch")
        self.image_optimization.setAccessibleName("图像优化")
        self.image_optimization.setEnabled(_IMAGE_OPTIMIZATION_GUI_UNLOCKED)
        self.image_optimization.setToolTip(
            "高负载图像优化：启用后使用 Target Identity → Ownership → Gallery 多模态图片链。"
            "当前版本暂未开放。"
        )
        self.image_optimization.toggled.connect(self._image_optimization_toggled)

        self.image_optimization_state = QLabel("默认关闭 · 高负载 · 当前暂未开放")
        self.image_optimization_state.setObjectName("imageOptimizationState")
        self.image_optimization_state.setWordWrap(True)

        optimization_row = QWidget()
        optimization_layout = QHBoxLayout(optimization_row)
        optimization_layout.setContentsMargins(0, 0, 0, 0)
        optimization_layout.setSpacing(10)
        optimization_layout.addWidget(self.image_optimization, 0, Qt.AlignmentFlag.AlignVCenter)
        optimization_layout.addWidget(self.image_optimization_state, 1, Qt.AlignmentFlag.AlignVCenter)

        self.key_action = QPushButton("显示")
        self.key_action.setObjectName("modalPrimaryButton")
        self.key_action.setMaximumWidth(72)
        self.key_action.clicked.connect(self._key_action_clicked)
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(8)
        key_row.addWidget(self.api_key, 1)
        key_row.addWidget(self.key_action)

        rows: list[tuple[str, Any]] = [
            ("API 类型", provider),
            ("Base URL", self.base_url),
            ("主模型", self.model),
            ("事实模型", self.fact_model),
            ("Web 搜索模型", self.web_model),
            ("图像优化", optimization_row),
        ]
        for row, (text, widget) in enumerate(rows):
            label = QLabel(text)
            label.setObjectName("modalFieldLabel")
            form.addWidget(label, row, 0, Qt.AlignmentFlag.AlignVCenter)
            form.addWidget(widget, row, 1)

        key_label = QLabel("API Key")
        key_label.setObjectName("modalFieldLabel")
        form.addWidget(key_label, len(rows), 0, Qt.AlignmentFlag.AlignVCenter)
        form.addLayout(key_row, len(rows), 1)
        layout.addWidget(card)

        self.key_status = QLabel()
        self.key_status.setObjectName("modalMetaLabel")
        self.key_status.setWordWrap(True)
        layout.addWidget(self.key_status)

        advanced_hint = QLabel(
            "高级 · 中转站兼容。默认关闭时 Fact / Web 继续复用上面的连接与密钥，旧配置无需迁移。"
            "只有明确开启某个角色后，才使用该角色自己的 Base URL 与加密密钥。"
        )
        advanced_hint.setObjectName("aiSettingsHint")
        advanced_hint.setWordWrap(True)
        layout.addWidget(advanced_hint)

        advanced = QFrame()
        advanced.setObjectName("cardDetailSection")
        advanced_form = QGridLayout(advanced)
        advanced_form.setContentsMargins(15, 14, 15, 15)
        advanced_form.setHorizontalSpacing(16)
        advanced_form.setVerticalSpacing(10)
        advanced_form.setColumnStretch(1, 1)

        self.fact_override = QCheckBox("独立 Fact 连接")
        self.fact_override.setObjectName("imageOptimizationSwitch")
        self.fact_override.toggled.connect(self._sync_override_controls)
        self.fact_base_url = self._input("Fact 中转 Base URL")
        fact_key_row = self._role_key_row("fact", "Fact API Key")
        fact_status = self._role_key_status["fact"]

        self.web_override = QCheckBox("独立 Web 连接")
        self.web_override.setObjectName("imageOptimizationSwitch")
        self.web_override.toggled.connect(self._sync_override_controls)
        self.web_base_url = self._input("兼容 DashScope Responses Web Search 的 Base URL")
        web_key_row = self._role_key_row("web", "Web API Key")
        web_status = self._role_key_status["web"]

        advanced_rows: list[tuple[str, Any]] = [
            ("Fact", self.fact_override),
            ("Fact Base URL", self.fact_base_url),
            ("Fact API Key", fact_key_row),
            ("", fact_status),
            ("Web", self.web_override),
            ("Web Base URL", self.web_base_url),
            ("Web API Key", web_key_row),
            ("", web_status),
        ]
        for row, (text, widget) in enumerate(advanced_rows):
            label = QLabel(text)
            label.setObjectName("modalFieldLabel")
            advanced_form.addWidget(label, row, 0, Qt.AlignmentFlag.AlignVCenter)
            advanced_form.addWidget(widget, row, 1)
        layout.addWidget(advanced)

        web_policy = QLabel(
            "Web 独立连接仍使用 DashScope Responses `web_search` 能力。普通 OpenAI-compatible 中转如果没有"
            "兼容该能力，会明确失败，不会静默退化为普通对话模型或伪造联网结果。"
        )
        web_policy.setObjectName("cardDetailText")
        web_policy.setWordWrap(True)
        layout.addWidget(web_policy)

        policy = QLabel(
            "正式客户端只使用这里配置的用户密钥。图像优化默认关闭时继续使用原有图片选择路径，"
            "不会启动额外的 Ownership / Gallery 多模态调用。以后如果提供平台内置 AI 额度，"
            "由服务器端 Gateway 做鉴权、配额、限流和计费，不把平台上游 Key 放进客户端。"
        )
        policy.setObjectName("cardDetailText")
        policy.setWordWrap(True)
        layout.addWidget(policy)
        layout.addStretch(1)

        actions = QHBoxLayout()
        self.clear_key_button = QPushButton("清除 API Key")
        self.clear_key_button.setObjectName("modalDangerButton")
        self.clear_key_button.clicked.connect(self._clear_key)
        self.save_button = QPushButton("保存设置")
        self.save_button.setObjectName("modalPrimaryButton")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.clear_key_button)
        actions.addStretch(1)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)

        self.reload()

    @staticmethod
    def _input(placeholder: str) -> QLineEdit:
        editor = QLineEdit()
        editor.setObjectName("aiSettingsInput")
        editor.setPlaceholderText(placeholder)
        return editor

    def _role_key_row(self, role: str, placeholder: str) -> QWidget:
        editor = self._input(placeholder)
        editor.setEchoMode(QLineEdit.EchoMode.Password)
        action = QPushButton("显示")
        action.setObjectName("modalPrimaryButton")
        action.setMaximumWidth(72)
        action.clicked.connect(lambda _checked=False, name=role: self._role_key_action_clicked(name))
        status = QLabel()
        status.setObjectName("aiRoleStatus")
        status.setWordWrap(True)
        self._role_key_inputs[role] = editor
        self._role_key_actions[role] = action
        self._role_key_status[role] = status

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        row_layout.addWidget(editor, 1)
        row_layout.addWidget(action)
        return row

    def reload(self) -> None:
        try:
            settings = load_ai_service_settings()
        except Exception as exc:
            QMessageBox.warning(self, "AI 设置读取失败", str(exc))
            settings = AIServiceSettings()
        self.base_url.setText(settings.base_url)
        self.model.setText(settings.model)
        self.fact_model.setText(settings.fact_model)
        self.web_model.setText(settings.web_model)
        self.fact_override.setChecked(settings.fact_override_enabled)
        self.fact_base_url.setText(settings.fact_base_url)
        self.web_override.setChecked(settings.web_override_enabled)
        self.web_base_url.setText(settings.web_base_url)
        self._set_key_state(has_ai_service_key(settings))
        self._set_role_key_state("fact", has_ai_service_role_key("fact"))
        self._set_role_key_state("web", has_ai_service_role_key("web"))
        self._refresh_key_status(settings)
        self._sync_override_controls()
        self._sync_image_optimization()

    def _sync_image_optimization(self) -> None:
        enabled = bool(_IMAGE_OPTIMIZATION_GUI_UNLOCKED and image_optimization_enabled())
        blocked = self.image_optimization.blockSignals(True)
        try:
            self.image_optimization.setChecked(enabled)
        finally:
            self.image_optimization.blockSignals(blocked)
        self.image_optimization.setEnabled(_IMAGE_OPTIMIZATION_GUI_UNLOCKED)
        if _IMAGE_OPTIMIZATION_GUI_UNLOCKED:
            self.image_optimization_state.setText(
                "已开启 · 使用高负载 AI 图片链" if enabled else "已关闭 · 使用原有图片路径"
            )
        else:
            self.image_optimization_state.setText("默认关闭 · 高负载 · 当前暂未开放")

    def _image_optimization_toggled(self, checked: bool) -> None:
        enabled = bool(checked and _IMAGE_OPTIMIZATION_GUI_UNLOCKED)
        set_image_optimization_enabled(enabled)
        if checked != enabled:
            self._sync_image_optimization()
            return
        self.image_optimization_state.setText(
            "已开启 · 使用高负载 AI 图片链" if enabled else "已关闭 · 使用原有图片路径"
        )

    def _set_key_state(self, configured: bool) -> None:
        self._key_configured = bool(configured)
        self._editing_key = not self._key_configured
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        if self._key_configured:
            self.api_key.setReadOnly(True)
            self.api_key.setText(_MASKED_KEY)
            self.api_key.setPlaceholderText("")
            self.key_action.setText("更改")
            self.key_action.setToolTip("输入新的 API Key 并在保存后覆盖当前密钥")
        else:
            self.api_key.setReadOnly(False)
            self.api_key.clear()
            self.api_key.setPlaceholderText("输入你自己的 API Key")
            self.key_action.setText("显示")
            self.key_action.setToolTip("显示 / 隐藏当前正在输入的新密钥")

    def _set_role_key_state(self, role: str, configured: bool) -> None:
        editor = self._role_key_inputs[role]
        action = self._role_key_actions[role]
        status = self._role_key_status[role]
        self._role_key_configured[role] = bool(configured)
        self._role_key_editing[role] = not bool(configured)
        editor.setEchoMode(QLineEdit.EchoMode.Password)
        if configured:
            editor.setReadOnly(True)
            editor.setText(_MASKED_KEY)
            editor.setPlaceholderText("")
            action.setText("更改")
            status.setText("独立密钥已安全保存；关闭覆盖不会删除密钥。")
        else:
            editor.setReadOnly(False)
            editor.clear()
            editor.setPlaceholderText(f"{role.title()} API Key")
            action.setText("显示")
            status.setText("尚未保存独立密钥。只有开启该角色覆盖时才需要。")

    def _begin_key_edit(self) -> None:
        self._editing_key = True
        self.api_key.setReadOnly(False)
        self.api_key.clear()
        self.api_key.setPlaceholderText("输入新的 API Key，保存后覆盖当前密钥")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_action.setText("显示")
        self.key_action.setToolTip("显示 / 隐藏当前正在输入的新密钥")
        self.api_key.setFocus(Qt.FocusReason.OtherFocusReason)
        self.key_status.setText("API Key · 正在更改。输入新密钥后点击“保存设置”即可覆盖旧密钥。")
        self.key_status.setStyleSheet("color: #b9d9f2; font-weight: 650;")

    def _begin_role_key_edit(self, role: str) -> None:
        self._role_key_editing[role] = True
        editor = self._role_key_inputs[role]
        action = self._role_key_actions[role]
        editor.setReadOnly(False)
        editor.clear()
        editor.setPlaceholderText(f"输入新的 {role.title()} API Key")
        editor.setEchoMode(QLineEdit.EchoMode.Password)
        action.setText("显示")
        editor.setFocus(Qt.FocusReason.OtherFocusReason)
        self._role_key_status[role].setText("正在更改；保存设置后覆盖旧密钥。")

    def _key_action_clicked(self) -> None:
        if self._key_configured and not self._editing_key:
            self._begin_key_edit()
            return
        visible = self.api_key.echoMode() == QLineEdit.EchoMode.Password
        self.api_key.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        self.key_action.setText("隐藏" if visible else "显示")

    def _role_key_action_clicked(self, role: str) -> None:
        if self._role_key_configured[role] and not self._role_key_editing[role]:
            self._begin_role_key_edit(role)
            return
        editor = self._role_key_inputs[role]
        action = self._role_key_actions[role]
        visible = editor.echoMode() == QLineEdit.EchoMode.Password
        editor.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        action.setText("隐藏" if visible else "显示")

    def _sync_override_controls(self, *_args) -> None:
        for role, enabled, base_editor in (
            ("fact", self.fact_override.isChecked(), self.fact_base_url),
            ("web", self.web_override.isChecked(), self.web_base_url),
        ):
            base_editor.setEnabled(enabled)
            self._role_key_inputs[role].setEnabled(enabled)
            self._role_key_actions[role].setEnabled(enabled)
            self._role_key_status[role].setEnabled(enabled)

    def _refresh_key_status(self, settings: AIServiceSettings | None = None) -> None:
        configured = settings or load_ai_service_settings()
        if has_ai_service_key(configured):
            self.key_status.setText(
                "API Key · 已安全保存。需要替换时点击“更改”，旧密钥不会在界面中回显。"
            )
            self.key_status.setStyleSheet("color: #9fe2bd; font-weight: 650;")
        else:
            self.key_status.setText("API Key · 未配置。Single / Batch 的 AI 阶段将保持锁定。")
            self.key_status.setStyleSheet("color: #f4cb7a; font-weight: 650;")

    def _save(self) -> None:
        settings = AIServiceSettings(
            provider="openai-compatible",
            base_url=self.base_url.text().strip(),
            model=self.model.text().strip(),
            fact_model=self.fact_model.text().strip(),
            web_model=self.web_model.text().strip(),
            fact_override_enabled=self.fact_override.isChecked(),
            fact_base_url=self.fact_base_url.text().strip(),
            web_override_enabled=self.web_override.isChecked(),
            web_base_url=self.web_base_url.text().strip(),
        )

        api_key: str | None = None
        if self._editing_key:
            value = self.api_key.text().strip()
            if not value:
                if self._key_configured:
                    QMessageBox.warning(
                        self,
                        "API Key 尚未更改",
                        "请输入新的 API Key；如不想更改当前密钥，关闭设置页即可。",
                    )
                    return
                QMessageBox.warning(self, "API Key 未配置", "请填写你自己的 API Key。")
                return
            api_key = value

        role_values: dict[str, str | None] = {"fact": None, "web": None}
        for role, enabled in (
            ("fact", settings.fact_override_enabled),
            ("web", settings.web_override_enabled),
        ):
            if self._role_key_editing[role]:
                value = self._role_key_inputs[role].text().strip()
                if value:
                    role_values[role] = value
                elif enabled and not self._role_key_configured[role]:
                    QMessageBox.warning(
                        self,
                        f"{role.title()} API Key 未配置",
                        f"已开启 {role.title()} 独立连接，请填写该角色自己的 API Key。",
                    )
                    return
                elif enabled and self._role_key_configured[role]:
                    QMessageBox.warning(
                        self,
                        f"{role.title()} API Key 尚未更改",
                        "请输入新的密钥；如不想更改旧密钥，请重新打开设置页后直接保存。",
                    )
                    return
            elif enabled and not self._role_key_configured[role]:
                QMessageBox.warning(
                    self,
                    f"{role.title()} API Key 未配置",
                    f"已开启 {role.title()} 独立连接，请先配置独立密钥。",
                )
                return

        try:
            settings.validated()
            for role, value in role_values.items():
                if value is not None:
                    save_ai_service_role_key(role, value)
            normalized = save_ai_service_settings(settings, api_key=api_key)
            if not has_ai_service_key(normalized):
                raise ValueError("请填写你自己的 API Key。")
        except Exception as exc:
            QMessageBox.critical(self, "AI 设置无法保存", str(exc))
            return

        self._set_key_state(True)
        self._set_role_key_state("fact", has_ai_service_role_key("fact"))
        self._set_role_key_state("web", has_ai_service_role_key("web"))
        self._sync_override_controls()
        self.key_status.setText("AI 服务配置已保存 · 密钥已安全保存，可随时点击“更改”进行替换。")
        self.key_status.setStyleSheet("color: #9fe2bd; font-weight: 700;")

    def _clear_key(self) -> None:
        answer = QMessageBox.question(
            self,
            "清除 API Key",
            "确定删除本机保存的主 AI API Key？独立 Fact / Web 密钥不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        clear_ai_service_key()
        os.environ.pop(_RUNTIME_KEY_ENV, None)
        self._set_key_state(False)
        self._refresh_key_status()


class AISettingsModalController:
    """Bind AI runtime configuration to the shared detail modal and both workflows."""

    def __init__(self, window) -> None:
        self.window = window
        # Every GUI launch starts on the legacy image path. The visible switch is
        # intentionally locked for now; once unlocked, it owns this process gate
        # and child Resolver processes inherit the state mechanically.
        set_image_optimization_enabled(False)
        self.button = QPushButton("设置")
        self.button.setObjectName("quietButton")
        self.button.setToolTip("AI 服务 / API Key / 图像优化")
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
            eyebrow="SETTINGS · AI SERVICE",
            populate=populate,
            ratio=(0.72, 0.82),
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

    def _apply_runtime(self, config) -> None:
        settings, api_key = resolved_ai_runtime()
        config.provider = "openai-compatible"
        config.base_url = settings.base_url
        config.local_model = settings.model
        config.fact_model = settings.fact_model
        config.web_model = settings.web_model
        config.api_key_env = _RUNTIME_KEY_ENV
        os.environ[_RUNTIME_KEY_ENV] = api_key

        self._clear_role_runtime()
        if settings.fact_override_enabled:
            fact_key = load_ai_service_role_key("fact")
            if not fact_key:
                raise ValueError("Fact 独立连接已启用，但没有可用的 Fact API Key。")
            os.environ[RUNTIME_FACT_BASE_URL_ENV] = settings.fact_base_url
            os.environ[RUNTIME_FACT_KEY_ENV] = fact_key
        if settings.web_override_enabled:
            web_key = load_ai_service_role_key("web")
            if not web_key:
                raise ValueError("Web 独立连接已启用，但没有可用的 Web API Key。")
            os.environ[RUNTIME_WEB_BASE_URL_ENV] = settings.web_base_url
            os.environ[RUNTIME_WEB_KEY_ENV] = web_key

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
