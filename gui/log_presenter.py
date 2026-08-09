from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QMainWindow, QPlainTextEdit


_LOG_FLUSH_MS = 70
_MAX_VISIBLE_LOG_BLOCKS = 12000
_MAX_BATCH_LINES = 400


class BufferedLogPresenter(QObject):
    """Batch subprocess log UI work so output bursts do not stall painting."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.runner = window.runner  # type: ignore[attr-defined]
        self.view: QPlainTextEdit = window.log_view  # type: ignore[attr-defined]
        self.pending: list[str] = []

        self.view.setUndoRedoEnabled(False)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.document().setMaximumBlockCount(_MAX_VISIBLE_LOG_BLOCKS)

        try:
            self.runner.log.disconnect(window._append_log)  # type: ignore[attr-defined]
        except (RuntimeError, TypeError):
            pass
        self.runner.log.connect(self.enqueue)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(_LOG_FLUSH_MS)
        self.timer.timeout.connect(self.flush)
        window.destroyed.connect(self.flush)

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
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.view.document().isEmpty():
            cursor.insertText("\n")
        cursor.insertText("\n".join(lines))
        self.view.setTextCursor(cursor)

        if was_at_bottom:
            bar.setValue(bar.maximum())


def install_buffered_logs(window: QMainWindow) -> BufferedLogPresenter:
    presenter = BufferedLogPresenter(window)
    window._buffered_logs = presenter  # type: ignore[attr-defined]
    return presenter
