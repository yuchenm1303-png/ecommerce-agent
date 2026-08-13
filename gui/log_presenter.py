from __future__ import annotations

from collections import deque

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QLabel, QMainWindow, QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget


_LOG_FLUSH_MS = 70
_CATCHUP_FLUSH_MS = 16
_MAX_VISIBLE_LOG_BLOCKS = 12000
_MAX_BATCH_LINES = 400
_MAX_CATCHUP_LINES = 800
_MAX_HIDDEN_PENDING = 8000
_MINI_MAX_VISIBLE_LOG_BLOCKS = 240
_MINI_MAX_BATCH_LINES = 120
_MINI_MAX_CATCHUP_LINES = 240
_MINI_MAX_HIDDEN_PENDING = 600


_MINI_LOG_STYLE = r"""
QPlainTextEdit#runtimeMiniLog {
    color: rgba(255,255,255,212);
    background-color: rgba(0,0,0,30);
    border: 1px solid rgba(255,255,255,13);
    border-radius: 8px;
    padding: 7px 8px;
    selection-background-color: rgba(255,255,255,44);
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 10px;
}
"""


class BufferedLogPresenter(QObject):
    """Batch subprocess log UI work so output bursts do not stall painting.

    The full Live Console and the compact Runtime card keep independent queues.
    This lets the Runtime card show the newest task output continuously without
    forcing the large hidden console document to repaint on every log burst.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.runner = window.runner  # type: ignore[attr-defined]
        self.view: QPlainTextEdit = window.log_view  # type: ignore[attr-defined]
        self.pending: deque[str] = deque()
        self._dropped_hidden = 0

        self.mini_view = self._install_runtime_mini_log(window)
        self.mini_pending: deque[str] = deque()
        self._mini_dropped_hidden = 0

        self.view.setUndoRedoEnabled(False)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.document().setMaximumBlockCount(_MAX_VISIBLE_LOG_BLOCKS)
        self.view.installEventFilter(self)

        if self.mini_view is not None:
            self.mini_view.installEventFilter(self)
            self.runner.running_changed.connect(self._on_running_changed)

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

    @staticmethod
    def _install_runtime_mini_log(window: QMainWindow) -> QPlainTextEdit | None:
        anchor = getattr(window, "cold_label", None)
        runtime_page = anchor.parentWidget() if isinstance(anchor, QWidget) else None
        layout = runtime_page.layout() if runtime_page is not None else None
        if runtime_page is None or not isinstance(layout, QVBoxLayout):
            return None

        # The old Cold/Hot/cache rows remain alive because result population still
        # owns them, but they no longer consume presentation space in the Runtime
        # page. The card now focuses on the actual task stream.
        for label in runtime_page.findChildren(QLabel):
            if label.objectName() == "cardHint":
                label.hide()

        view = QPlainTextEdit(runtime_page)
        view.setObjectName("runtimeMiniLog")
        view.setReadOnly(True)
        view.setUndoRedoEnabled(False)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        view.setPlaceholderText("等待任务运行…")
        view.document().setMaximumBlockCount(_MINI_MAX_VISIBLE_LOG_BLOCKS)
        view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        view.setStyleSheet(_MINI_LOG_STYLE)
        layout.addWidget(view, 1)
        setattr(window, "runtime_log_view", view)
        return view

    def _on_running_changed(self, running: bool) -> None:
        if not running or self.mini_view is None:
            return
        self.mini_pending.clear()
        self._mini_dropped_hidden = 0
        self.mini_view.clear()

    @staticmethod
    def _trim_hidden(
        pending: deque[str],
        limit: int,
        dropped: int,
    ) -> int:
        if len(pending) <= limit:
            return dropped
        drop = len(pending) - limit
        for _ in range(drop):
            pending.popleft()
        return dropped + drop

    def enqueue(self, line: str) -> None:
        self.pending.append(line)
        if self.mini_view is not None:
            self.mini_pending.append(line)

        if not self.view.isVisible():
            self._dropped_hidden = self._trim_hidden(
                self.pending,
                _MAX_HIDDEN_PENDING,
                self._dropped_hidden,
            )

        if self.mini_view is not None and not self.mini_view.isVisible():
            self._mini_dropped_hidden = self._trim_hidden(
                self.mini_pending,
                _MINI_MAX_HIDDEN_PENDING,
                self._mini_dropped_hidden,
            )

        primary_ready = self.view.isVisible() and len(self.pending) >= _MAX_BATCH_LINES
        mini_ready = (
            self.mini_view is not None
            and self.mini_view.isVisible()
            and len(self.mini_pending) >= _MINI_MAX_BATCH_LINES
        )
        any_visible = self.view.isVisible() or (
            self.mini_view is not None and self.mini_view.isVisible()
        )

        if primary_ready or mini_ready:
            self.flush()
        elif any_visible and not self.timer.isActive():
            self.timer.start(_LOG_FLUSH_MS)
        elif not any_visible and self.timer.isActive():
            self.timer.stop()

    @staticmethod
    def _append_lines(
        view: QPlainTextEdit,
        lines: list[str],
        *,
        force_bottom: bool = False,
    ) -> None:
        if not lines:
            return

        bar = view.verticalScrollBar()
        was_at_bottom = bar.value() >= bar.maximum() - 6
        cursor = view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not view.document().isEmpty():
            cursor.insertText("\n")
        cursor.insertText("\n".join(lines))
        view.setTextCursor(cursor)

        if force_bottom or was_at_bottom:
            bar.setValue(bar.maximum())

    def _flush_primary(self) -> None:
        if not self.pending or not self.view.isVisible():
            return

        prefix: list[str] = []
        if self._dropped_hidden:
            prefix.append(
                f"[UI] {self._dropped_hidden} older hidden log lines were omitted for responsiveness."
            )
            self._dropped_hidden = 0

        batch_count = min(_MAX_CATCHUP_LINES, len(self.pending))
        batch = [self.pending.popleft() for _ in range(batch_count)]
        self._append_lines(self.view, [*prefix, *batch])

    def _flush_mini(self) -> None:
        if self.mini_view is None or not self.mini_pending or not self.mini_view.isVisible():
            return

        prefix: list[str] = []
        if self._mini_dropped_hidden:
            prefix.append(f"[UI] 已省略 {self._mini_dropped_hidden} 条较早日志")
            self._mini_dropped_hidden = 0

        batch_count = min(_MINI_MAX_CATCHUP_LINES, len(self.mini_pending))
        batch = [self.mini_pending.popleft() for _ in range(batch_count)]
        self._append_lines(self.mini_view, [*prefix, *batch], force_bottom=True)

    def flush(self) -> None:
        if self.timer.isActive():
            self.timer.stop()

        self._flush_primary()
        self._flush_mini()

        primary_waiting = self.view.isVisible() and bool(self.pending)
        mini_waiting = (
            self.mini_view is not None
            and self.mini_view.isVisible()
            and bool(self.mini_pending)
        )
        if primary_waiting or mini_waiting:
            self.timer.start(_CATCHUP_FLUSH_MS)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show:
            if watched is self.view and self.pending:
                QTimer.singleShot(0, self.flush)
            elif watched is self.mini_view and self.mini_pending:
                QTimer.singleShot(0, self.flush)
        return False

    def _cleanup(self) -> None:
        self.timer.stop()
        for view in (self.view, self.mini_view):
            if view is None:
                continue
            try:
                view.removeEventFilter(self)
            except RuntimeError:
                pass
        # Do not force hidden widget repaints during teardown.
        if self.view.isVisible() or (
            self.mini_view is not None and self.mini_view.isVisible()
        ):
            self.flush()


def install_buffered_logs(window: QMainWindow) -> BufferedLogPresenter:
    presenter = BufferedLogPresenter(window)
    window._buffered_logs = presenter  # type: ignore[attr-defined]
    return presenter
