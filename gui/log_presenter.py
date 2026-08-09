from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QMainWindow, QPlainTextEdit


_LOG_FLUSH_MS = 70
_CATCHUP_FLUSH_MS = 16
_MAX_VISIBLE_LOG_BLOCKS = 12000
_MAX_BATCH_LINES = 400
_MAX_CATCHUP_LINES = 800
_MAX_HIDDEN_PENDING = 8000


class BufferedLogPresenter(QObject):
    """Batch subprocess log UI work so output bursts do not stall painting."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.runner = window.runner  # type: ignore[attr-defined]
        self.view: QPlainTextEdit = window.log_view  # type: ignore[attr-defined]
        self.pending: list[str] = []
        self._dropped_hidden = 0

        self.view.setUndoRedoEnabled(False)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.document().setMaximumBlockCount(_MAX_VISIBLE_LOG_BLOCKS)
        self.view.installEventFilter(self)

        try:
            self.runner.log.disconnect(window._append_log)  # type: ignore[attr-defined]
        except (RuntimeError, TypeError):
            pass
        self.runner.log.connect(self.enqueue)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(_LOG_FLUSH_MS)
        self.timer.timeout.connect(self.flush)
        window.destroyed.connect(self._cleanup)

    def enqueue(self, line: str) -> None:
        self.pending.append(line)

        # Collapsed console / non-selected Live Console tab: keep the data but do
        # zero document/layout work. Cap memory and retain the newest output.
        if not self.view.isVisible():
            if len(self.pending) > _MAX_HIDDEN_PENDING:
                drop = len(self.pending) - _MAX_HIDDEN_PENDING
                del self.pending[:drop]
                self._dropped_hidden += drop
            if self.timer.isActive():
                self.timer.stop()
            return

        if len(self.pending) >= _MAX_BATCH_LINES:
            self.flush()
        elif not self.timer.isActive():
            self.timer.start(_LOG_FLUSH_MS)

    def flush(self) -> None:
        if not self.pending or not self.view.isVisible():
            if self.timer.isActive():
                self.timer.stop()
            return
        if self.timer.isActive():
            self.timer.stop()

        prefix: list[str] = []
        if self._dropped_hidden:
            prefix.append(
                f"[UI] {self._dropped_hidden} older hidden log lines were omitted for responsiveness."
            )
            self._dropped_hidden = 0

        batch = self.pending[:_MAX_CATCHUP_LINES]
        del self.pending[: len(batch)]
        lines = [*prefix, *batch]

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

        if self.pending:
            self.timer.start(_CATCHUP_FLUSH_MS)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.view and event.type() == QEvent.Type.Show and self.pending:
            QTimer.singleShot(0, self.flush)
        return False

    def _cleanup(self) -> None:
        self.timer.stop()
        try:
            self.view.removeEventFilter(self)
        except RuntimeError:
            pass
        # Do not force a hidden widget repaint during teardown.
        if self.view.isVisible():
            self.flush()


def install_buffered_logs(window: QMainWindow) -> BufferedLogPresenter:
    presenter = BufferedLogPresenter(window)
    window._buffered_logs = presenter  # type: ignore[attr-defined]
    return presenter
