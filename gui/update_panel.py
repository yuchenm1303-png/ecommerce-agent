from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


_PANEL_STYLE = """
QFrame#updatePanelCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(18, 24, 38, 252), stop:1 rgba(30, 39, 62, 252));
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 22px;
}
QLabel#updateEyebrow {
    color: rgba(161, 182, 224, 210);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#updateTitle {
    color: rgba(248, 250, 255, 255);
    font-size: 23px;
    font-weight: 700;
}
QLabel#updateSubtitle {
    color: rgba(186, 199, 226, 225);
    font-size: 12px;
}
QLabel#updateBody {
    color: rgba(224, 231, 246, 235);
    font-size: 13px;
    line-height: 1.35;
}
QLabel#updateMeta {
    color: rgba(154, 171, 207, 210);
    font-size: 11px;
}
QPushButton#updatePrimary {
    min-height: 38px;
    padding: 0 20px;
    color: white;
    background: rgba(83, 140, 255, 235);
    border: 1px solid rgba(141, 179, 255, 150);
    border-radius: 11px;
    font-size: 12px;
    font-weight: 700;
}
QPushButton#updatePrimary:hover { background: rgba(102, 154, 255, 245); }
QPushButton#updatePrimary:pressed { background: rgba(72, 126, 232, 245); }
QPushButton#updateSecondary {
    min-height: 38px;
    padding: 0 18px;
    color: rgba(232, 238, 250, 230);
    background: rgba(255, 255, 255, 12);
    border: 1px solid rgba(255, 255, 255, 28);
    border-radius: 11px;
    font-size: 12px;
    font-weight: 650;
}
QPushButton#updateSecondary:hover { background: rgba(255, 255, 255, 22); }
QPushButton#updateClose {
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    color: rgba(216, 225, 244, 190);
    background: transparent;
    border: 0;
    border-radius: 9px;
    font-size: 19px;
}
QPushButton#updateClose:hover { background: rgba(255, 255, 255, 18); color: white; }
QProgressBar#updateProgress {
    min-height: 8px;
    max-height: 8px;
    background: rgba(255, 255, 255, 16);
    border: 0;
    border-radius: 4px;
    text-align: center;
}
QProgressBar#updateProgress::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(87, 142, 255, 255), stop:1 rgba(133, 102, 255, 255));
    border-radius: 4px;
}
"""


class _UpdatePanelBase(QDialog):
    def __init__(self, parent: QWidget | None, *, title: str, subtitle: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Listing Studio")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(_PANEL_STYLE)
        self.setMinimumWidth(610)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)

        self.card = QFrame(self)
        self.card.setObjectName("updatePanelCard")
        root.addWidget(self.card)

        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(30, 28, 30, 28)
        self.card_layout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(16)
        icon_label = QLabel(self.card)
        icon_label.setFixedSize(58, 58)
        app = QApplication.instance()
        icon = app.windowIcon() if app is not None else self.windowIcon()
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(58, 58))
        header.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        heading = QVBoxLayout()
        heading.setSpacing(3)
        eyebrow = QLabel("LISTING STUDIO · SECURE UPDATE", self.card)
        eyebrow.setObjectName("updateEyebrow")
        heading.addWidget(eyebrow)
        self.title_label = QLabel(title, self.card)
        self.title_label.setObjectName("updateTitle")
        heading.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle, self.card)
        self.subtitle_label.setObjectName("updateSubtitle")
        self.subtitle_label.setWordWrap(True)
        heading.addWidget(self.subtitle_label)
        header.addLayout(heading, 1)
        self.card_layout.addLayout(header)

    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            frame = self.frameGeometry()
            frame.moveCenter(parent.frameGeometry().center())
            self.move(frame.topLeft())
        self.raise_()
        self.activateWindow()


class UpdateOfferDialog(_UpdatePanelBase):
    def __init__(
        self,
        parent: QWidget,
        *,
        current_version: str,
        target_version: str,
        package_size: str = "",
        notes: str = "",
    ) -> None:
        super().__init__(
            parent,
            title=f"新版本 v{target_version} 已准备好",
            subtitle="下载由 Velopack 安全校验，安装完成后 Listing Studio 会自动重新打开。",
        )
        self.setMinimumHeight(350)

        meta = QLabel(self.card)
        meta.setObjectName("updateMeta")
        meta_text = f"当前版本  v{current_version}     →     目标版本  v{target_version}"
        if package_size:
            meta_text += f"     ·     {package_size}"
        meta.setText(meta_text)
        self.card_layout.addWidget(meta)

        body = QLabel(self.card)
        body.setObjectName("updateBody")
        body.setWordWrap(True)
        body.setText(notes[:1800] if notes else "本次更新将保持你的账号、日志与浏览器工作区数据。")
        body.setMinimumHeight(80)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.card_layout.addWidget(body, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        later = QPushButton("稍后", self.card)
        later.setObjectName("updateSecondary")
        later.clicked.connect(self.reject)
        buttons.addWidget(later)
        install = QPushButton("立即更新", self.card)
        install.setObjectName("updatePrimary")
        install.setDefault(True)
        install.clicked.connect(self.accept)
        buttons.addWidget(install)
        self.card_layout.addLayout(buttons)


class UpdateProgressDialog(_UpdatePanelBase):
    def __init__(self, parent: QWidget, *, target_version: str) -> None:
        super().__init__(
            parent,
            title=f"正在更新到 v{target_version}",
            subtitle="请保持 Listing Studio 打开。下载完成后程序会自动重启。",
        )
        self.setMinimumHeight(300)

        self.stage_label = QLabel("正在准备安全下载…", self.card)
        self.stage_label.setObjectName("updateBody")
        self.stage_label.setWordWrap(True)
        self.card_layout.addWidget(self.stage_label)

        self.progress = QProgressBar(self.card)
        self.progress.setObjectName("updateProgress")
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        self.card_layout.addWidget(self.progress)

        self.percent_label = QLabel("0%", self.card)
        self.percent_label.setObjectName("updateMeta")
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.card_layout.addWidget(self.percent_label)

        hint = QLabel("下载、校验、版本切换和回滚由 Velopack 管理。", self.card)
        hint.setObjectName("updateMeta")
        self.card_layout.addWidget(hint)

    def set_progress(self, value: int) -> None:
        percent = max(0, min(100, int(value)))
        self.progress.setValue(percent)
        self.percent_label.setText(f"{percent}%")

    def set_stage(self, text: str) -> None:
        self.stage_label.setText(str(text))
        self.raise_()
        self.activateWindow()

    def reject(self) -> None:
        # The update panel cannot be dismissed while package handoff is active.
        return


class UpdateMessageDialog(_UpdatePanelBase):
    def __init__(
        self,
        parent: QWidget,
        *,
        title: str,
        message: str = "",
        details: str = "",
    ) -> None:
        super().__init__(parent, title=title, subtitle=message)
        self.setMinimumHeight(270)
        if details:
            body = QLabel(details, self.card)
            body.setObjectName("updateBody")
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.card_layout.addWidget(body)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        ok = QPushButton("确定", self.card)
        ok.setObjectName("updatePrimary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        self.card_layout.addLayout(buttons)


__all__ = ["UpdateMessageDialog", "UpdateOfferDialog", "UpdateProgressDialog"]
