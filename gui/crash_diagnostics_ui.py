from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QByteArray, Qt, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.crash_diagnostics import acknowledge_pending_report
from gui.app_access import ApplicationAccessController


class CrashReportDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        *,
        access: ApplicationAccessController,
        report: dict[str, Any],
    ) -> None:
        super().__init__(parent)
        self.access = access
        self.report = report
        self.report_code = ""
        self.network = QNetworkAccessManager(self)

        self.setWindowTitle("Listing Studio · 诊断")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        title = QLabel("Listing Studio 上次异常退出", self)
        title.setObjectName("diagTitle")
        body = QLabel(
            "程序已经自动保存了一份诊断报告。你不需要查日志或打开事件查看器，"
            "点击“发送诊断报告”即可把问题信息交给我们。",
            self,
        )
        body.setObjectName("diagBody")
        body.setWordWrap(True)

        stage = str(report.get("last_stage") or "unknown")
        version = str(report.get("app_version") or "unknown")
        self.meta = QLabel(f"异常版本：v{version}    ·    最后阶段：{stage}", self)
        self.meta.setObjectName("diagMeta")
        self.meta.setWordWrap(True)

        privacy = QLabel(
            "诊断只包含程序版本、Windows 环境、启动阶段和崩溃堆栈；"
            "不会发送商品链接、账号密码、AI 内容或客户文件。",
            self,
        )
        privacy.setObjectName("diagPrivacy")
        privacy.setWordWrap(True)

        self.status = QLabel("报告仅保存在本机，发送成功后会得到一个诊断编号。", self)
        self.status.setObjectName("diagStatus")
        self.status.setWordWrap(True)

        self.later_button = QPushButton("稍后", self)
        self.later_button.clicked.connect(self.reject)
        self.send_button = QPushButton("发送诊断报告", self)
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self._send)
        self.copy_button = QPushButton("复制诊断编号", self)
        self.copy_button.setVisible(False)
        self.copy_button.clicked.connect(self._copy_code)

        buttons = QHBoxLayout()
        buttons.addWidget(self.later_button)
        buttons.addStretch(1)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.send_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addWidget(self.meta)
        layout.addWidget(privacy)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

        self.setStyleSheet(
            """
            QDialog { background: #121a26; color: #f2f6ff; }
            QLabel#diagTitle { font-size: 22px; font-weight: 700; }
            QLabel#diagBody { color: #d6e0f2; font-size: 13px; }
            QLabel#diagMeta, QLabel#diagPrivacy { color: #93a6c5; font-size: 11px; }
            QLabel#diagStatus { color: #b8c8df; font-size: 12px; }
            QPushButton { min-height: 38px; padding: 0 16px; border-radius: 9px;
                          border: 1px solid #34445d; background: #1b2636; color: #eaf1ff; }
            QPushButton:hover { background: #243249; }
            QPushButton:default { background: #3976dc; border-color: #6b9af1; color: white; }
            QPushButton:disabled { color: #76849a; background: #192231; }
            """
        )

    def _payload(self) -> dict[str, Any]:
        session = self.access.session
        return {
            "action": "diagnostic",
            "user_id": session.user_id,
            "device_id": session.device_id,
            "telemetry_token": session.telemetry_token,
            "app_version": self.access.installed_version,
            "diagnostic": self.report,
        }

    def _send(self) -> None:
        session = self.access.session
        if not (
            session.enforced
            and session.user_id
            and session.device_id
            and session.telemetry_token
        ):
            self.status.setText("当前没有可用的正式授权会话，报告仍保存在本机。")
            return

        self.send_button.setEnabled(False)
        self.later_button.setEnabled(False)
        self.status.setText("正在安全发送诊断报告…")

        request = QNetworkRequest(QUrl(self.access.telemetry_function_url))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        reply = self.network.post(
            request,
            QByteArray(json.dumps(self._payload(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
        )
        reply.finished.connect(lambda: self._finished(reply))

    def _finished(self, reply: QNetworkReply) -> None:
        try:
            raw = bytes(reply.readAll()).decode("utf-8", "replace")
            payload = json.loads(raw) if raw else {}
        except (ValueError, json.JSONDecodeError):
            payload = {}
        ok = reply.error() == QNetworkReply.NetworkError.NoError and bool(payload.get("accepted"))
        if not ok:
            code = str(payload.get("error") or reply.errorString() or "network_error")
            self.status.setText(f"暂时发送失败（{code}）。报告仍保存在本机，下次启动可以再次发送。")
            self.send_button.setEnabled(True)
            self.later_button.setEnabled(True)
            reply.deleteLater()
            return

        self.report_code = str(payload.get("report_code") or "").strip()
        acknowledge_pending_report(str(self.report.get("crash_id") or ""))
        self.status.setText(
            f"诊断已发送。编号：{self.report_code or '已接收'}。"
            "如需联系支持，只要告诉我们这个编号即可。"
        )
        self.send_button.setVisible(False)
        self.copy_button.setVisible(bool(self.report_code))
        self.later_button.setText("完成")
        self.later_button.setEnabled(True)
        reply.deleteLater()

    def _copy_code(self) -> None:
        if not self.report_code:
            return
        QApplication.clipboard().setText(self.report_code)
        self.status.setText(f"诊断编号 {self.report_code} 已复制。")


def offer_pending_crash_report(
    parent: QWidget,
    *,
    access: ApplicationAccessController,
    report: dict[str, Any] | None,
) -> None:
    if not isinstance(report, dict) or not report.get("crash_id"):
        return
    dialog = CrashReportDialog(parent, access=access, report=report)
    dialog.exec()


__all__ = ["CrashReportDialog", "offer_pending_crash_report"]
