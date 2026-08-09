from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QMainWindow, QPlainTextEdit

from .optimized_cursor_fx import install_optimized_cursor_fx


_LOG_FLUSH_MS = 70
_MAX_VISIBLE_LOG_BLOCKS = 4500
_MAX_BATCH_LINES = 250


class BufferedLogPresenter(QObject):
    """Batch subprocess log UI work so output bursts do not stall Qt painting."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.runner = window.runner  # type: ignore[attr-defined]
        self.view: QPlainTextEdit = window.log_view  # type: ignore[attr-defined]
        self.pending: list[str] = []

        self.view.setUndoRedoEnabled(False)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.document().setMaximumBlockCount(_MAX_VISIBLE_LOG_BLOCKS)

        # Replace per-line appendPlainText + per-line scrollbar movement with a
        # single document insertion every ~70 ms.
        try:
            self.runner.log.disconnect(window._append_log)  # type: ignore[attr-defined]
        except (RuntimeError, TypeError):
            pass
        self.runner.log.connect(self.enqueue)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(_LOG_FLUSH_MS)
        self.timer.timeout.connect(self.flush)

    def enqueue(self, line: str) -> None:
        self.pending.append(line)
        if len(self.pending) >= _MAX_BATCH_LINES:
            self.flush()
        elif not self.timer.isActive():
            self.timer.start()

    def flush(self) -> None:
        if not self.pending:
            return
        if self.timer.isActive():
            self.timer.stop()

        lines = self.pending
        self.pending = []
        bar = self.view.verticalScrollBar()
        was_at_bottom = bar.value() >= bar.maximum() - 6

        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.End)
        if not self.view.document().isEmpty():
            cursor.insertText("\n")
        cursor.insertText("\n".join(lines))
        self.view.setTextCursor(cursor)

        if was_at_bottom:
            bar.setValue(bar.maximum())


class GuiPerformanceController(QObject):
    def __init__(self, window: QMainWindow, visual_fx) -> None:
        super().__init__(window)
        self.window = window
        self.visual_fx = visual_fx

        # The legacy follower overlay repainted the whole application at 60 fps
        # even after its petal count was set to zero. Keep its native white-dot
        # cursor installation, but stop/hide that expensive paint layer.
        legacy_overlay = visual_fx.overlay
        legacy_overlay.timer.stop()
        legacy_overlay.hide()

        self.cursor = install_optimized_cursor_fx(window)
        self.logs = BufferedLogPresenter(window)

        window.destroyed.connect(self._cleanup)

    def raise_cursor(self) -> None:
        self.cursor.raise_()

    def _cleanup(self) -> None:
        self.logs.flush()


def install_gui_performance_tuning(window: QMainWindow, visual_fx) -> GuiPerformanceController:
    controller = GuiPerformanceController(window, visual_fx)
    window._gui_performance = controller  # type: ignore[attr-defined]
    return controller
