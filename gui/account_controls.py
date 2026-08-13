from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .app_access import (
    AccessError,
    AccessNetworkError,
    ApplicationAccessController,
    _LoginDialog,
    _OFFLINE_RETRY_INTERVAL_MS,
    _REVALIDATE_INTERVAL_MS,
    _clear_state,
    _friendly_error,
    _license_check,
)


class _AccountDialog(QDialog):
    SWITCH_ACCOUNT = 11
    LOG_OUT = 12
    RELEASE_AND_LOG_OUT = 13

    def __init__(self, parent: QWidget, controller: ApplicationAccessController) -> None:
        super().__init__(parent)
        self.controller = controller
        session = controller.session

        self.setWindowTitle("Listing Studio · 账户")
        self.setModal(True)
        self.setFixedWidth(460)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        title = QLabel("账户与设备")
        title.setObjectName("accountDialogTitle")
        email = QLabel(session.email or "已登录账户")
        email.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        email.setObjectName("accountDialogEmail")

        state_text = "离线宽限中" if session.offline_grace else "已授权"
        device_text = (
            f"{state_text} · 当前设备 {session.device_name or 'Windows PC'} · "
            f"已激活 {session.active_devices}/{session.max_devices} 台"
        )
        detail = QLabel(device_text)
        detail.setWordWrap(True)
        detail.setObjectName("accountDialogDetail")

        switch_button = QPushButton("切换账户")
        switch_button.setObjectName("accountPrimaryButton")
        logout_button = QPushButton("退出登录")
        release_button = QPushButton("退出并释放此设备")
        release_button.setObjectName("accountDangerButton")
        close_button = QPushButton("关闭")

        switch_button.clicked.connect(lambda: self.done(self.SWITCH_ACCOUNT))
        logout_button.clicked.connect(lambda: self.done(self.LOG_OUT))
        release_button.clicked.connect(lambda: self.done(self.RELEASE_AND_LOG_OUT))
        close_button.clicked.connect(self.reject)

        actions = QVBoxLayout()
        actions.setSpacing(9)
        actions.addWidget(switch_button)
        actions.addWidget(logout_button)
        actions.addWidget(release_button)

        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(email)
        layout.addWidget(detail)
        layout.addSpacing(4)
        layout.addLayout(actions)
        layout.addSpacing(4)
        layout.addLayout(footer)

        self.setStyleSheet(
            """
            QDialog { background: #111923; color: #eef5fb; }
            QLabel#accountDialogTitle { font-size: 22px; font-weight: 720; }
            QLabel#accountDialogEmail { color: #f4fbff; font-size: 14px; font-weight: 650; }
            QLabel#accountDialogDetail { color: #9eb0bf; line-height: 1.5; }
            QPushButton { min-height: 38px; padding: 0 14px; border: 1px solid #334858; border-radius: 9px; background: #1c2a36; color: #eaf4fb; }
            QPushButton:hover { background: #263846; }
            QPushButton#accountPrimaryButton { background: #2f7282; border-color: #63b8ca; }
            QPushButton#accountDangerButton { background: #61343e; border-color: #8b5662; }
            """
        )


class ApplicationAccountControls(QObject):
    def __init__(self, window: QWidget, controller: ApplicationAccessController) -> None:
        super().__init__(window)
        self.window = window
        self.controller = controller
        self.button: QPushButton | None = None

        if controller.session.enforced:
            self._install_header_button()

    def _install_header_button(self) -> None:
        root = self.window.centralWidget()
        outer = root.layout() if root is not None else None
        if outer is None or outer.count() <= 0:
            return
        header = outer.itemAt(0).layout()
        if not isinstance(header, QHBoxLayout):
            return

        button = QPushButton()
        button.setObjectName("accountHeaderButton")
        button.setMinimumHeight(36)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self._open_account_dialog)
        button.setStyleSheet(
            """
            QPushButton#accountHeaderButton {
                min-height: 34px;
                padding: 0 12px;
                border-radius: 11px;
                border: 1px solid rgba(255,255,255,42);
                background-color: rgba(48,57,69,110);
                color: rgba(255,255,255,226);
                font-weight: 650;
            }
            QPushButton#accountHeaderButton:hover {
                background-color: rgba(65,79,92,145);
                border-color: rgba(255,255,255,68);
            }
            """
        )
        header.insertWidget(max(0, header.count() - 1), button, 0, Qt.AlignmentFlag.AlignBottom)
        self.button = button
        self._sync_button()

    def _sync_button(self) -> None:
        if self.button is None:
            return
        session = self.controller.session
        identity = session.display_name.strip() or session.email.split("@", 1)[0] or "账户"
        if len(identity) > 18:
            identity = f"{identity[:17]}…"
        state = "离线" if session.offline_grace else "已授权"
        self.button.setText(f"{identity} · {state}")
        self.button.setToolTip(
            f"{session.email}\n"
            f"设备：{session.device_name or 'Windows PC'}\n"
            f"已激活：{session.active_devices}/{session.max_devices}"
        )

    def _open_account_dialog(self) -> None:
        dialog = _AccountDialog(self.window, self.controller)
        result = dialog.exec()
        if result == _AccountDialog.SWITCH_ACCOUNT:
            self._switch_account()
        elif result == _AccountDialog.LOG_OUT:
            self._logout()
        elif result == _AccountDialog.RELEASE_AND_LOG_OUT:
            self._release_and_logout()

    def _switch_account(self) -> None:
        login = _LoginDialog()
        login.setWindowTitle("Listing Studio · 切换账户")
        login.email.clear()
        if login.exec() != QDialog.DialogCode.Accepted or login.session is None:
            return

        self.controller.timer.stop()
        self.controller.session = login.session
        self.controller._schedule(  # noqa: SLF001 - same-package access controller integration
            _OFFLINE_RETRY_INTERVAL_MS
            if login.session.offline_grace
            else _REVALIDATE_INTERVAL_MS
        )
        self._sync_button()
        QMessageBox.information(
            self.window,
            "Listing Studio · 已切换账户",
            f"当前账户已切换为：\n{login.session.email}\n\n"
            "原账户的设备授权仍保留；如需释放，请先切回原账户后选择“退出并释放此设备”。",
        )

    def _logout(self) -> None:
        answer = QMessageBox.question(
            self.window,
            "Listing Studio · 退出登录",
            "确认退出当前账户吗？\n\n"
            "本机登录凭据会被清除，但当前设备仍保留在该账户的设备列表中。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.controller.timer.stop()
        _clear_state()
        QApplication.quit()

    def _release_and_logout(self) -> None:
        answer = QMessageBox.warning(
            self.window,
            "Listing Studio · 释放当前设备",
            "这会退出当前账户，并立即释放这台电脑占用的设备名额。\n\n"
            "以后仍可再次登录并重新激活此设备。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        token = self.controller.bearer_token()
        if not token:
            QMessageBox.warning(
                self.window,
                "Listing Studio · 无法释放设备",
                "当前无法取得有效登录令牌。请检查网络后重试；如果只想退出，可使用“退出登录”。",
            )
            return

        session = self.controller.session
        try:
            _license_check(
                token,
                action="deactivate",
                device_id=session.device_id,
                device_name=session.device_name,
            )
        except AccessNetworkError:
            QMessageBox.warning(
                self.window,
                "Listing Studio · 无法释放设备",
                "无法连接授权服务器。设备名额尚未释放，请联网后重试。",
            )
            return
        except AccessError as exc:
            QMessageBox.warning(
                self.window,
                "Listing Studio · 无法释放设备",
                _friendly_error(exc),
            )
            return

        self.controller.timer.stop()
        _clear_state()
        QMessageBox.information(
            self.window,
            "Listing Studio · 设备已释放",
            "当前设备名额已释放，登录状态已清除。",
        )
        QApplication.quit()


def install_application_account_controls(
    window: QWidget,
    controller: ApplicationAccessController,
) -> ApplicationAccountControls | None:
    if not controller.session.enforced:
        return None
    existing = getattr(window, "_application_account_controls", None)
    if isinstance(existing, ApplicationAccountControls):
        return existing
    controls = ApplicationAccountControls(window, controller)
    window._application_account_controls = controls  # type: ignore[attr-defined]
    return controls


__all__ = ["ApplicationAccountControls", "install_application_account_controls"]
