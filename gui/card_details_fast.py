from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTabWidget,
    QWidget,
)

from .card_details import CardDetailController
from .overlay_sheet_motion import ClipSheetMotion


_GEOMETRY_COALESCE_MS = 32
_FOCUS_MARGIN = 28

_FOCUS_STYLE = r"""
QFrame#cardDetailScrim {
    background-color: rgba(7,10,16,150);
    border: 0;
}
QFrame#cardDetailDrawer {
    background-color: rgba(16,18,22,244);
    border: 1px solid rgba(255,255,255,34);
    border-radius: 16px;
}
QLabel#cardDetailTitle {
    font-size: 22px;
    font-weight: 740;
}
"""


class FastCardDetailController(CardDetailController):
    """Spotlight detail mode with zero main-layout animation.

    The selected card never changes geometry. Other cards stay in place behind a
    dim scrim while one final-size detail panel is progressively revealed by an
    absolute clipping viewport. Text, tables and the main splitters never see an
    intermediate animation size.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)

        self.root.setStyleSheet(self.root.styleSheet() + "\n" + _FOCUS_STYLE)

        # Remove the old QWidget opacity/geometry animation surfaces. The focus
        # panel itself stays at final size inside a clipping viewport.
        self.drawer.setGraphicsEffect(None)
        self.drawer_effect = None  # type: ignore[assignment]
        self.ghost.setGraphicsEffect(None)
        self.ghost_effect = None  # type: ignore[assignment]
        self.ghost.hide()

        self._motion = ClipSheetMotion(
            self.root,
            self.drawer,
            self._focus_rect,
            edge="focus",
            duration_ms=176,
            origin_provider=self._focus_origin_rect,
        )
        self._motion.opened.connect(self._finish_open)
        self._motion.closed.connect(self._finish_close)

        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(_GEOMETRY_COALESCE_MS)
        self._geometry_timer.timeout.connect(self._sync_geometry)

        console_toggle = getattr(window, "console_detail_toggle", None)
        console = getattr(window, "console", None)
        if isinstance(console_toggle, QPushButton) and isinstance(console, QFrame):
            console_toggle.clicked.connect(lambda *_: self.open(console))

    def _focus_rect(self) -> QRect:
        root = self.root.rect()
        max_width = max(1, root.width() - 2 * _FOCUS_MARGIN)
        max_height = max(1, root.height() - 2 * _FOCUS_MARGIN)
        frame = self._selected

        if frame is getattr(self.window, "console", None):
            width_ratio, height_ratio = 0.94, 0.84
        elif frame in {
            getattr(self.window, "ready_card", None),
            getattr(self.window, "missing_card", None),
            getattr(self.window, "conflict_card", None),
            getattr(self.window, "blocked_card", None),
        }:
            width_ratio, height_ratio = 0.74, 0.70
        elif isinstance(frame, QFrame) and getattr(self.window, "field_table", None) in frame.findChildren(QTableWidget):
            width_ratio, height_ratio = 0.90, 0.82
        elif isinstance(frame, QFrame) and frame.objectName() == "microCard":
            width_ratio, height_ratio = 0.72, 0.68
        else:
            width_ratio, height_ratio = 0.84, 0.76

        width = min(max_width, max(560, int(root.width() * width_ratio)))
        height = min(max_height, max(360, int(root.height() * height_ratio)))
        x = max(_FOCUS_MARGIN, (root.width() - width) // 2)
        y = max(_FOCUS_MARGIN, (root.height() - height) // 2)
        return QRect(x, y, width, height)

    def _focus_origin_rect(self) -> QRect:
        return self._card_rect(self._selected)

    def _schedule_geometry(self) -> None:
        if not self._geometry_timer.isActive():
            self._geometry_timer.start()

    def _sync_geometry(self) -> None:
        self.scrim.setGeometry(self.root.rect())
        for frame in self._installed_cards:
            self._position_button(frame)
        self._motion.sync_geometry()

    def _stop_animation(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()
        self._motion.stop()
        self.ghost.hide()

    def _clone_text_view(self, source: QPlainTextEdit, title: str) -> None:
        text = source.toPlainText().strip()
        if not text:
            return
        lines = text.splitlines()
        clipped = "\n".join(lines[-320:])
        layout = self._section(title)
        clone = QPlainTextEdit()
        clone.setObjectName("cardDetailTextView")
        clone.setReadOnly(True)
        clone.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        clone.setPlainText(clipped)
        clone.setMinimumHeight(220)
        clone.setMaximumHeight(430)
        layout.addWidget(clone)

    def _populate_console_focus(self, frame: QFrame) -> None:
        self._clear_body()
        self.title.setText("运行控制台 · 深度详情")
        self.eyebrow.setText("ACCEPTANCE CONTROL · FOCUS MODE")

        phase_lines: list[str] = []
        for unit in getattr(frame, "phase_units", {}).values():
            title = getattr(unit, "title", None)
            state = getattr(unit, "state", None)
            detail = getattr(unit, "detail", None)
            title_text = title.text().strip() if isinstance(title, QLabel) else "Phase"
            state_text = state.text().strip() if isinstance(state, QLabel) else ""
            detail_text = detail.text().strip() if isinstance(detail, QLabel) else ""
            line = " · ".join(part for part in (title_text, state_text, detail_text) if part)
            if line:
                phase_lines.append(line)
        self._add_text_lines("四阶段状态", phase_lines)

        progress_detail = getattr(frame, "progress_detail", None)
        if isinstance(progress_detail, QLabel) and progress_detail.text().strip():
            self._add_text_lines("当前进度", [progress_detail.text().strip()])

        tabs = getattr(frame, "tabs", None)
        if isinstance(tabs, QTabWidget):
            for index in range(tabs.count()):
                page = tabs.widget(index)
                if not isinstance(page, QWidget):
                    continue
                tab_name = tabs.tabText(index).strip() or f"Tab {index + 1}"

                labels: list[str] = []
                seen: set[str] = set()
                for label in page.findChildren(QLabel):
                    text = label.text().strip()
                    if not text or text in seen:
                        continue
                    seen.add(text)
                    labels.append(text)
                if labels:
                    self._add_text_lines(f"{tab_name} · 摘要", labels[:14])

                for table_index, table in enumerate(page.findChildren(QTableWidget)[:3]):
                    explicit = str(table.property("detailTitle") or "").strip()
                    suffix = "" if table_index == 0 else f" {table_index + 1}"
                    self._clone_table(table, title=explicit or f"{tab_name} · 数据{suffix}")

                for text_index, view in enumerate(page.findChildren(QPlainTextEdit)[:3]):
                    explicit = str(view.property("detailTitle") or "").strip()
                    suffix = "" if text_index == 0 else f" {text_index + 1}"
                    self._clone_text_view(view, explicit or f"{tab_name} · 文本{suffix}")

        if self.body_layout.count() == 0:
            self._add_text_lines("详情", ["当前暂无运行详情；执行流程后重新打开即可查看完整诊断。"])
        self.body_layout.addStretch(1)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    def _populate_focus(self, frame: QFrame) -> None:
        if frame is getattr(self.window, "console", None):
            self._populate_console_focus(frame)
        else:
            self._populate(frame)

    def open(self, frame: QFrame) -> None:
        if frame not in self._buttons:
            return

        anchored = getattr(self.window, "_anchored_sheets", None)
        if anchored is not None and hasattr(anchored, "close_all"):
            anchored.close_all()

        self._stop_animation()
        self._selected = frame
        self._populate_focus(frame)
        self.body_layout.activate()
        if self.drawer.layout() is not None:
            self.drawer.layout().activate()
        self.scroll.verticalScrollBar().setValue(0)

        self.scrim.setGeometry(self.root.rect())
        self.scrim.show()
        self.scrim.raise_()
        self._motion.open()
        self._motion.viewport.raise_()
        self.ghost.hide()

    def _finish_open(self) -> None:
        self._motion.viewport.raise_()
        self.close_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def close(self) -> None:
        if self._selected is None and not self.scrim.isVisible():
            return
        self._motion.close()

    def _finish_close(self) -> None:
        self.scrim.hide()
        self.ghost.hide()
        self._selected = None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.root:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show}:
                self._schedule_geometry()
            elif event_type == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() == Qt.Key.Key_Escape and self.scrim.isVisible():
                    self.close()
                    return True
        elif isinstance(watched, QFrame) and watched in self._buttons:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest}:
                self._schedule_geometry()
        return False

    def _cleanup(self) -> None:
        self._geometry_timer.stop()
        self._motion.cleanup()
        super()._cleanup()


def install_card_details(window: QMainWindow) -> FastCardDetailController:
    controller = FastCardDetailController(window)
    window._card_details = controller  # type: ignore[attr-defined]
    window.destroyed.connect(controller._cleanup)
    return controller
