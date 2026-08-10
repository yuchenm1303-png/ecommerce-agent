from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEvent,
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


_EXPANDABLE_NAMES = {"glassCard", "heroCard", "statusCard", "microCard", "consolePhaseUnit"}
_OPEN_MS = 190
_CLOSE_MS = 165
_DRAWER_MAX_WIDTH = 720
_DRAWER_MIN_WIDTH = 470
_MARGIN = 18


_DETAIL_STYLE = r"""
QFrame#cardDetailScrim {
    background-color: rgba(0,0,0,34);
    border: 0;
}
QFrame#cardDetailGhost {
    background-color: rgba(255,255,255,18);
    border: 1px solid rgba(255,255,255,48);
    border-radius: 12px;
}
QFrame#cardDetailDrawer {
    background-color: rgba(0,0,0,118);
    border: 1px solid rgba(255,255,255,34);
    border-radius: 14px;
}
QFrame#cardDetailSection {
    background-color: rgba(255,255,255,10);
    border: 1px solid rgba(255,255,255,14);
    border-radius: 9px;
}
QLabel#cardDetailEyebrow {
    color: rgba(255,255,255,142);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#cardDetailTitle {
    color: #ffffff;
    font-size: 20px;
    font-weight: 730;
}
QLabel#cardDetailSectionTitle {
    color: rgba(255,255,255,226);
    font-size: 11px;
    font-weight: 700;
}
QLabel#cardDetailText {
    color: rgba(255,255,255,188);
    font-size: 11px;
}
QToolButton#cardExpandButton {
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    padding: 0;
    color: rgba(255,255,255,178);
    background-color: rgba(0,0,0,42);
    border: 1px solid rgba(255,255,255,18);
    border-radius: 7px;
    font-size: 14px;
    font-weight: 700;
}
QToolButton#cardExpandButton:hover {
    color: #ffffff;
    background-color: rgba(255,255,255,34);
    border-color: rgba(255,255,255,42);
}
QToolButton#cardExpandButton:pressed {
    background-color: rgba(255,255,255,22);
}
QToolButton#cardDetailClose {
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    color: #ffffff;
    background-color: rgba(255,255,255,18);
    border: 1px solid rgba(255,255,255,20);
    border-radius: 8px;
    font-size: 17px;
}
QToolButton#cardDetailClose:hover {
    background-color: rgba(255,255,255,34);
}
QScrollArea#cardDetailScroll {
    background: transparent;
    border: 0;
}
QWidget#cardDetailBody {
    background: transparent;
}
QTableWidget#cardDetailTable {
    color: rgba(255,255,255,230);
    background-color: rgba(0,0,0,48);
    alternate-background-color: rgba(255,255,255,7);
    border: 1px solid rgba(255,255,255,12);
    border-radius: 7px;
    gridline-color: transparent;
    selection-background-color: rgba(255,255,255,34);
}
QTableWidget#cardDetailTable::item {
    padding: 7px 9px;
    border-bottom: 1px solid rgba(255,255,255,8);
}
QTableWidget#cardDetailTable QHeaderView::section {
    min-height: 36px;
    padding: 0 9px;
    color: rgba(255,255,255,218);
    background-color: rgba(255,255,255,24);
    border: 0;
    font-size: 10px;
    font-weight: 700;
}
QPlainTextEdit#cardDetailTextView {
    color: rgba(255,255,255,214);
    background-color: rgba(0,0,0,50);
    border: 1px solid rgba(255,255,255,12);
    border-radius: 7px;
    padding: 9px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 10px;
}
"""


class _Scrim(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self.clicked.emit()
        event.accept()


class CardDetailController(QObject):
    """One reusable animated detail drawer for every presentation card.

    The source card never changes layout geometry. A short-lived transition
    ghost provides the card-expansion motion while the drawer slides/fades in.
    That keeps QWidget layout, splitters and the Quick glass mask out of the
    animation hot path.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.root = window.centralWidget()
        if self.root is None:
            raise RuntimeError("Card details require a central widget")

        self._selected: QFrame | None = None
        self._animation: QParallelAnimationGroup | None = None
        self._buttons: dict[QFrame, QToolButton] = {}
        self._installed_cards: list[QFrame] = []

        self.scrim = _Scrim(self.root)
        self.scrim.setObjectName("cardDetailScrim")
        self.scrim.hide()
        self.scrim.clicked.connect(self.close)

        self.ghost = QFrame(self.root)
        self.ghost.setObjectName("cardDetailGhost")
        self.ghost_effect = QGraphicsOpacityEffect(self.ghost)
        self.ghost.setGraphicsEffect(self.ghost_effect)
        self.ghost.hide()

        self.drawer = QFrame(self.root)
        self.drawer.setObjectName("cardDetailDrawer")
        self.drawer_effect = QGraphicsOpacityEffect(self.drawer)
        self.drawer.setGraphicsEffect(self.drawer_effect)
        self.drawer.hide()

        drawer_layout = QVBoxLayout(self.drawer)
        drawer_layout.setContentsMargins(18, 16, 18, 18)
        drawer_layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.eyebrow = QLabel("CARD DETAIL")
        self.eyebrow.setObjectName("cardDetailEyebrow")
        self.title = QLabel("详情")
        self.title.setObjectName("cardDetailTitle")
        title_box.addWidget(self.eyebrow)
        title_box.addWidget(self.title)
        header.addLayout(title_box, 1)
        self.close_button = QToolButton()
        self.close_button.setObjectName("cardDetailClose")
        self.close_button.setText("×")
        self.close_button.setToolTip("关闭详情  ·  Esc")
        self.close_button.clicked.connect(self.close)
        header.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        drawer_layout.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("cardDetailScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.body.setObjectName("cardDetailBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(10)
        self.scroll.setWidget(self.body)
        drawer_layout.addWidget(self.scroll, 1)

        self.root.setStyleSheet(self.root.styleSheet() + "\n" + _DETAIL_STYLE)
        self.root.installEventFilter(self)
        self._install_cards()
        QTimer.singleShot(0, self._sync_geometry)

    def _install_cards(self) -> None:
        seen: set[int] = set()
        for frame in self.window.findChildren(QFrame):
            if frame.objectName() not in _EXPANDABLE_NAMES:
                continue
            if frame in {self.scrim, self.drawer, self.ghost} or id(frame) in seen:
                continue
            seen.add(id(frame))
            button = QToolButton(frame)
            button.setObjectName("cardExpandButton")
            button.setText("↗")
            button.setToolTip("展开卡片详情")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, card=frame: self.open(card))
            button.show()
            button.raise_()
            frame.installEventFilter(self)
            self._buttons[frame] = button
            self._installed_cards.append(frame)
            self._position_button(frame)

    def _position_button(self, frame: QFrame) -> None:
        button = self._buttons.get(frame)
        if button is None:
            return
        x = max(4, frame.width() - button.width() - 7)
        button.move(x, 6)
        button.raise_()

    def _sync_geometry(self) -> None:
        self.scrim.setGeometry(self.root.rect())
        for frame in self._installed_cards:
            self._position_button(frame)
        if self.drawer.isVisible() and self._animation is None:
            self.drawer.setGeometry(self._drawer_rect())

    def _drawer_rect(self) -> QRect:
        root = self.root.rect()
        available = max(320, root.width() - _MARGIN * 2)
        width = min(_DRAWER_MAX_WIDTH, max(_DRAWER_MIN_WIDTH, int(root.width() * 0.44)))
        width = min(width, available)
        height = max(320, root.height() - _MARGIN * 2)
        return QRect(root.width() - width - _MARGIN, _MARGIN, width, height)

    def _card_rect(self, frame: QFrame | None) -> QRect:
        if frame is None or not frame.isVisibleTo(self.root):
            target = self._drawer_rect()
            return QRect(target.right() - 40, target.center().y() - 20, 40, 40)
        top_left = frame.mapTo(self.root, frame.rect().topLeft())
        return QRect(top_left, frame.size())

    @staticmethod
    def _animation_for(
        target: QObject,
        prop: bytes,
        start: object,
        end: object,
        duration: int,
        easing: QEasingCurve.Type,
    ) -> QPropertyAnimation:
        animation = QPropertyAnimation(target, prop)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(duration)
        animation.setEasingCurve(easing)
        return animation

    def _stop_animation(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()

    def open(self, frame: QFrame) -> None:
        if frame not in self._buttons:
            return
        self._stop_animation()
        self._selected = frame
        self._populate(frame)

        source = self._card_rect(frame)
        target = self._drawer_rect()
        drawer_start = QRect(target)
        drawer_start.translate(34, 0)

        self.scrim.setGeometry(self.root.rect())
        self.scrim.show()
        self.scrim.raise_()
        self.ghost.setGeometry(source)
        self.ghost_effect.setOpacity(0.58)
        self.ghost.show()
        self.ghost.raise_()
        self.drawer.setGeometry(drawer_start)
        self.drawer_effect.setOpacity(0.0)
        self.drawer.show()
        self.drawer.raise_()

        group = QParallelAnimationGroup(self)
        group.addAnimation(
            self._animation_for(
                self.ghost,
                b"geometry",
                source,
                target,
                _OPEN_MS,
                QEasingCurve.Type.OutCubic,
            )
        )
        group.addAnimation(
            self._animation_for(
                self.ghost_effect,
                b"opacity",
                0.58,
                0.0,
                _OPEN_MS,
                QEasingCurve.Type.OutQuad,
            )
        )
        group.addAnimation(
            self._animation_for(
                self.drawer,
                b"geometry",
                drawer_start,
                target,
                _OPEN_MS,
                QEasingCurve.Type.OutCubic,
            )
        )
        group.addAnimation(
            self._animation_for(
                self.drawer_effect,
                b"opacity",
                0.0,
                1.0,
                _OPEN_MS - 25,
                QEasingCurve.Type.OutCubic,
            )
        )
        group.finished.connect(self._finish_open)
        self._animation = group
        group.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _finish_open(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.deleteLater()
        self.ghost.hide()
        self.drawer_effect.setOpacity(1.0)
        self.drawer.setGeometry(self._drawer_rect())
        self.drawer.raise_()
        self.close_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def close(self) -> None:
        if not self.drawer.isVisible() and not self.scrim.isVisible():
            return
        self._stop_animation()
        source = self._card_rect(self._selected)
        target = self._drawer_rect()
        drawer_end = QRect(target)
        drawer_end.translate(32, 0)

        self.ghost.setGeometry(target)
        self.ghost_effect.setOpacity(0.34)
        self.ghost.show()
        self.ghost.raise_()
        self.drawer.raise_()

        group = QParallelAnimationGroup(self)
        group.addAnimation(
            self._animation_for(
                self.drawer,
                b"geometry",
                self.drawer.geometry(),
                drawer_end,
                _CLOSE_MS,
                QEasingCurve.Type.InCubic,
            )
        )
        group.addAnimation(
            self._animation_for(
                self.drawer_effect,
                b"opacity",
                self.drawer_effect.opacity(),
                0.0,
                _CLOSE_MS - 20,
                QEasingCurve.Type.InCubic,
            )
        )
        group.addAnimation(
            self._animation_for(
                self.ghost,
                b"geometry",
                target,
                source,
                _CLOSE_MS,
                QEasingCurve.Type.InOutCubic,
            )
        )
        group.addAnimation(
            self._animation_for(
                self.ghost_effect,
                b"opacity",
                0.34,
                0.0,
                _CLOSE_MS,
                QEasingCurve.Type.InQuad,
            )
        )
        group.finished.connect(self._finish_close)
        self._animation = group
        group.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _finish_close(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.deleteLater()
        self.drawer.hide()
        self.ghost.hide()
        self.scrim.hide()
        self.drawer_effect.setOpacity(0.0)
        self._selected = None

    def _clear_body(self) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._delete_layout(child_layout)

    @classmethod
    def _delete_layout(cls, layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child is not None:
                cls._delete_layout(child)
        layout.deleteLater()

    def _section(self, title: str) -> QVBoxLayout:
        card = QFrame()
        card.setObjectName("cardDetailSection")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 11)
        layout.setSpacing(7)
        heading = QLabel(title)
        heading.setObjectName("cardDetailSectionTitle")
        layout.addWidget(heading)
        self.body_layout.addWidget(card)
        return layout

    def _add_text_lines(self, title: str, lines: Iterable[str]) -> None:
        clean = [str(line).strip() for line in lines if str(line).strip()]
        if not clean:
            return
        layout = self._section(title)
        for line in clean:
            label = QLabel(line)
            label.setObjectName("cardDetailText")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)

    def _clone_table(
        self,
        source: QTableWidget,
        *,
        title: str,
        accepted_rows: set[int] | None = None,
    ) -> None:
        rows = [
            row
            for row in range(source.rowCount())
            if accepted_rows is None or row in accepted_rows
        ]
        layout = self._section(title)
        table = QTableWidget(len(rows), source.columnCount())
        table.setObjectName("cardDetailTable")
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        labels = []
        for column in range(source.columnCount()):
            item = source.horizontalHeaderItem(column)
            labels.append(item.text() if item is not None else f"Column {column + 1}")
        table.setHorizontalHeaderLabels(labels)
        table.setUpdatesEnabled(False)
        for target_row, source_row in enumerate(rows):
            for column in range(source.columnCount()):
                source_item = source.item(source_row, column)
                value = source_item.text() if source_item is not None else ""
                item = QTableWidgetItem(value)
                if source_item is not None:
                    item.setToolTip(source_item.toolTip())
                    item.setForeground(source_item.foreground())
                table.setItem(target_row, column, item)
        table.setUpdatesEnabled(True)
        header = table.horizontalHeader()
        for column in range(source.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            source_width = source.columnWidth(column)
            table.setColumnWidth(column, max(90, min(260, source_width)))
        if source.columnCount():
            header.setSectionResizeMode(source.columnCount() - 1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setDefaultSectionSize(36)
        table.setMinimumHeight(min(430, max(118, 42 + len(rows) * 36)))
        table.setMaximumHeight(430)
        layout.addWidget(table)
        if not rows:
            empty = QLabel("当前没有对应数据。")
            empty.setObjectName("cardDetailText")
            layout.addWidget(empty)

    def _status_name(self, frame: QFrame) -> str | None:
        mapping = {
            getattr(self.window, "ready_card", None): "READY",
            getattr(self.window, "missing_card", None): "MISSING",
            getattr(self.window, "conflict_card", None): "CONFLICT",
            getattr(self.window, "blocked_card", None): "BLOCKED",
        }
        return mapping.get(frame)

    def _populate_status(self, frame: QFrame, status: str) -> None:
        value = getattr(frame, "value", None)
        count = value.text() if isinstance(value, QLabel) else "—"
        self._add_text_lines("状态摘要", [f"{status} = {count}"])
        table = getattr(self.window, "field_table", None)
        if not isinstance(table, QTableWidget):
            return
        accepted: set[int] = set()
        for row in range(table.rowCount()):
            values = {
                (table.item(row, column).text().strip().upper() if table.item(row, column) else "")
                for column in range(table.columnCount())
            }
            if status in values:
                accepted.add(row)
        self._clone_table(table, title=f"{status} 字段", accepted_rows=accepted)

    def _populate_controls(self, frame: QFrame) -> None:
        lines: list[str] = []
        for widget in frame.findChildren(QWidget):
            if isinstance(widget, QLineEdit):
                label = widget.placeholderText() or widget.objectName() or "Text"
                lines.append(f"{label}: {widget.text() or '—'}")
            elif isinstance(widget, QSpinBox):
                lines.append(f"{widget.prefix().strip() or 'Value'}: {widget.value()}")
            elif isinstance(widget, QComboBox):
                lines.append(f"Scope: {widget.currentText() or '—'}")
            elif isinstance(widget, QCheckBox):
                lines.append(f"{'✓' if widget.isChecked() else '○'}  {widget.text()}")
            elif isinstance(widget, QProgressBar):
                lines.append(f"Progress: {widget.value()}%  ·  {widget.format()}")
        self._add_text_lines("当前配置", lines)

    def _populate_labels(self, frame: QFrame) -> None:
        texts: list[str] = []
        seen: set[str] = set()
        for label in frame.findChildren(QLabel):
            if not label.isVisibleTo(frame):
                continue
            text = label.text().strip()
            if not text or text in seen:
                continue
            if label.objectName() in {"sectionEyebrow", "cardTitle", "consoleEyebrow", "consoleTitle"}:
                continue
            seen.add(text)
            texts.append(text)
        self._add_text_lines("当前状态", texts[:30])

    def _populate_text_views(self, frame: QFrame) -> None:
        for index, view in enumerate(frame.findChildren(QPlainTextEdit)):
            if not view.isVisibleTo(frame):
                continue
            text = view.toPlainText().strip()
            if not text:
                continue
            lines = text.splitlines()
            clipped = "\n".join(lines[-240:])
            layout = self._section("日志 / 文本" if index == 0 else f"文本 {index + 1}")
            clone = QPlainTextEdit()
            clone.setObjectName("cardDetailTextView")
            clone.setReadOnly(True)
            clone.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            clone.setPlainText(clipped)
            clone.setMinimumHeight(190)
            clone.setMaximumHeight(360)
            layout.addWidget(clone)

    def _populate(self, frame: QFrame) -> None:
        self._clear_body()
        status = self._status_name(frame)
        title, eyebrow = self._card_identity(frame, status)
        self.title.setText(title)
        self.eyebrow.setText(eyebrow)

        if status is not None:
            self._populate_status(frame, status)
        else:
            self._populate_labels(frame)
            self._populate_controls(frame)
            tables = [
                table
                for table in frame.findChildren(QTableWidget)
                if table.isVisibleTo(frame)
            ]
            for index, table in enumerate(tables[:3]):
                self._clone_table(table, title="完整数据" if index == 0 else f"数据表 {index + 1}")
            self._populate_text_views(frame)

        if self.body_layout.count() == 0:
            self._add_text_lines("详情", ["当前卡片暂无额外数据；运行流程后重新打开即可查看最新状态。"])
        self.body_layout.addStretch(1)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    def _card_identity(self, frame: QFrame, status: str | None) -> tuple[str, str]:
        if status is not None:
            return f"{status} · 字段详情", "FINAL STATUS"

        title = ""
        eyebrow = "CARD DETAIL"
        for label in frame.findChildren(QLabel):
            if label.objectName() in {"cardTitle", "consoleTitle"} and label.text().strip():
                title = label.text().strip()
                break
        for label in frame.findChildren(QLabel):
            if label.objectName() in {"sectionEyebrow", "consoleEyebrow"} and label.text().strip():
                eyebrow = label.text().strip()
                break
        if not title and hasattr(frame, "title"):
            candidate = getattr(frame, "title")
            if isinstance(candidate, QLabel):
                title = candidate.text().strip()
        if not title:
            labels = [label.text().strip() for label in frame.findChildren(QLabel) if label.text().strip()]
            title = labels[0] if labels else "卡片详情"
        return title, eyebrow

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.root:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show}:
                QTimer.singleShot(0, self._sync_geometry)
            elif event_type == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() == Qt.Key.Key_Escape and self.drawer.isVisible():
                    self.close()
                    return True
        elif isinstance(watched, QFrame) and watched in self._buttons:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest}:
                QTimer.singleShot(0, lambda card=watched: self._position_button(card))
        return False

    def _cleanup(self) -> None:
        self._stop_animation()
        try:
            self.root.removeEventFilter(self)
        except RuntimeError:
            pass
        for frame in self._installed_cards:
            try:
                frame.removeEventFilter(self)
            except RuntimeError:
                pass


def install_card_details(window: QMainWindow) -> CardDetailController:
    controller = CardDetailController(window)
    window._card_details = controller  # type: ignore[attr-defined]
    window.destroyed.connect(controller._cleanup)
    return controller
