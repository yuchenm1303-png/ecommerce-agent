"""Independent top-level Runtime Assistant for progress and recovery telemetry."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRect, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.makro.runtime_contract import RuntimeEvent, RuntimeState
from .runtime_event_bridge import install_runtime_event_bridge
from .runtime_shadow_recovery import install_runtime_shadow_recovery


_URGENT_STATES = {
    RuntimeState.AI_ANALYZING,
    RuntimeState.RECOVERING,
    RuntimeState.WAITING_FOR_USER,
    RuntimeState.WARNING,
    RuntimeState.FAILED,
}


class RuntimeAssistant(QFrame):
    """Small independent OS tool window for runtime/recovery communication.

    It is deliberately not parented to the main GUI's central widget. The user
    can drag it anywhere on the desktop (including another monitor), and its
    saved position is restored on the next launch.
    """

    user_response = Signal(object)

    _COMPACT_WIDTH = 330
    _EXPANDED_WIDTH = 430
    _COMPACT_HEIGHT = 68
    _EXPANDED_HEIGHT = 246
    _SCREEN_MARGIN = 20

    def __init__(self, window: Any) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(None, flags)
        self.window = window
        self._expanded = False
        self._last_event: RuntimeEvent | None = None
        self._drag_offset: QPoint | None = None
        self._settings = QSettings("ecommerce-agent", "RuntimeAssistant")

        self.setWindowTitle("Runtime Assistant")
        self.setObjectName("runtimeAssistant")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self._COMPACT_WIDTH, self._COMPACT_HEIGHT)
        self.setToolTip("拖动空白区域可移动 Runtime Assistant")
        self.setStyleSheet(
            """
            QFrame#runtimeAssistant {
                background-color: rgba(8, 20, 36, 232);
                border: 1px solid rgba(204, 229, 255, 52);
                border-radius: 14px;
            }
            QLabel#runtimeAssistantState { color: #dcecff; font-weight: 700; }
            QLabel#runtimeAssistantMeta { color: rgba(222, 236, 252, 172); }
            QLabel#runtimeAssistantAlert { color: #f5d38c; font-weight: 700; }
            QPushButton#runtimeAssistantQuiet {
                min-height: 28px;
                padding: 0 10px;
                border-radius: 8px;
                color: rgba(235, 244, 255, 220);
                background: rgba(255, 255, 255, 18);
                border: 1px solid rgba(255, 255, 255, 30);
            }
            QPushButton#runtimeAssistantQuiet:hover {
                background: rgba(255, 255, 255, 30);
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(5)

        header = QHBoxLayout()
        self.state_label = QLabel("● IDLE")
        self.state_label.setObjectName("runtimeAssistantState")
        self.progress_label = QLabel("0%")
        self.progress_label.setObjectName("runtimeAssistantMeta")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.state_label)
        header.addStretch(1)
        header.addWidget(self.progress_label)
        layout.addLayout(header)

        self.detail_label = QLabel("等待商品任务")
        self.detail_label.setObjectName("runtimeAssistantMeta")
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.detail_label)

        self.alert_label = QLabel("")
        self.alert_label.setObjectName("runtimeAssistantAlert")
        self.alert_label.setTextFormat(Qt.TextFormat.PlainText)
        self.alert_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.alert_label.setWordWrap(True)
        layout.addWidget(self.alert_label)

        self.suggestion_title = QLabel("Recovery 建议")
        self.suggestion_title.setObjectName("runtimeAssistantMeta")
        layout.addWidget(self.suggestion_title)

        self.suggestion_label = QLabel("")
        self.suggestion_label.setObjectName("runtimeAssistantMeta")
        self.suggestion_label.setTextFormat(Qt.TextFormat.PlainText)
        self.suggestion_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.suggestion_label.setWordWrap(True)
        layout.addWidget(self.suggestion_label)

        self.confidence_label = QLabel("")
        self.confidence_label.setObjectName("runtimeAssistantMeta")
        layout.addWidget(self.confidence_label)

        actions = QHBoxLayout()
        self.collapse_button = QPushButton("收起")
        self.collapse_button.setObjectName("runtimeAssistantQuiet")
        self.collapse_button.clicked.connect(lambda: self.set_expanded(False))
        self.stop_button = QPushButton("停止当前任务")
        self.stop_button.setObjectName("runtimeAssistantQuiet")
        self.stop_button.clicked.connect(self._stop_current_task)
        actions.addStretch(1)
        actions.addWidget(self.collapse_button)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)

        self._expanded_widgets = (
            self.alert_label,
            self.suggestion_title,
            self.suggestion_label,
            self.confidence_label,
            self.collapse_button,
            self.stop_button,
        )
        self.set_expanded(False)

        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._compact_after_recovery)

        self.show()
        QTimer.singleShot(0, self._restore_or_place)

    @staticmethod
    def _screen_for_point(point: QPoint):
        return QApplication.screenAt(point) or QApplication.primaryScreen()

    def _window_rect_at(self, point: QPoint) -> QRect:
        return QRect(point.x(), point.y(), self.width(), self.height())

    def _position_is_visible(self, point: QPoint) -> bool:
        rect = self._window_rect_at(point)
        for screen in QApplication.screens():
            overlap = rect.intersected(screen.availableGeometry())
            if overlap.width() >= 80 and overlap.height() >= 40:
                return True
        return False

    def _restore_or_place(self) -> None:
        saved = self._settings.value("position")
        if isinstance(saved, QPoint) and self._position_is_visible(saved):
            self.move(saved)
        else:
            self._place_default()
        self._ensure_visible()
        self.raise_()

    def _place_default(self) -> None:
        try:
            center = self.window.frameGeometry().center()
        except RuntimeError:
            center = QPoint()
        screen = self._screen_for_point(center)
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.right() - self.width() - self._SCREEN_MARGIN + 1
        y = area.bottom() - self.height() - self._SCREEN_MARGIN + 1
        self.move(x, y)

    def _ensure_visible(self) -> None:
        center = self.frameGeometry().center()
        screen = self._screen_for_point(center)
        if screen is None:
            return
        area = screen.availableGeometry()
        max_x = max(area.left(), area.right() - self.width() + 1)
        max_y = max(area.top(), area.bottom() - self.height() + 1)
        x = min(max(self.x(), area.left()), max_x)
        y = min(max(self.y(), area.top()), max_y)
        self.move(x, y)

    def _save_position(self) -> None:
        try:
            if self.isVisible():
                self._settings.setValue("position", self.pos())
                self._settings.sync()
        except RuntimeError:
            pass

    @staticmethod
    def _is_button_child(widget: Any) -> bool:
        current = widget
        while current is not None:
            if isinstance(current, QPushButton):
                return True
            current = current.parentWidget() if hasattr(current, "parentWidget") else None
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if not self._is_button_child(child):
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.unsetCursor()
            self._ensure_visible()
            self._save_position()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        for widget in self._expanded_widgets:
            widget.setVisible(self._expanded)
        if self._expanded:
            self.setFixedSize(self._EXPANDED_WIDTH, self._EXPANDED_HEIGHT)
        else:
            self.setFixedSize(self._COMPACT_WIDTH, self._COMPACT_HEIGHT)
        if self.isVisible():
            self._ensure_visible()

    def present(self, event: RuntimeEvent) -> None:
        self._last_event = event
        state = event.state
        state_text = {
            RuntimeState.IDLE: "● IDLE",
            RuntimeState.RUNNING: "● RUNNING",
            RuntimeState.READY: "● READY",
            RuntimeState.AI_ANALYZING: "◉ AI ANALYZING",
            RuntimeState.RECOVERING: "◉ RECOVERING",
            RuntimeState.WAITING_FOR_USER: "⚠ ACTION REQUIRED",
            RuntimeState.RECOVERED: "✓ RECOVERED",
            RuntimeState.WARNING: "⚠ WARNING",
            RuntimeState.FAILED: "× SAFE STOP",
            RuntimeState.COMPLETE: "✓ COMPLETE",
        }.get(state, f"● {state.value}")
        self.state_label.setText(state_text)
        self.progress_label.setText(f"{event.progress}%")
        detail = event.title
        if event.phase:
            detail = f"{event.phase} · {detail}"
        self.detail_label.setText(detail)
        self.alert_label.setText(event.detail)
        advisor = str(event.advisor or "system").casefold()
        self.suggestion_title.setText("AI 建议" if advisor == "ai" else "Recovery 建议")
        self.suggestion_label.setText(event.suggestion or "当前没有额外恢复动作。")
        if event.confidence > 0:
            self.confidence_label.setText(
                f"Confidence {event.confidence * 100:.0f}% · {event.permission.value}"
            )
        else:
            self.confidence_label.setText(
                f"{event.permission.value} · Shadow Mode"
                if advisor in {"shadow", "rules"}
                else event.permission.value
            )

        urgent = state in _URGENT_STATES
        if urgent:
            self._settle_timer.stop()
            self.set_expanded(True)
        elif state is RuntimeState.RECOVERED:
            self.set_expanded(True)
            self._settle_timer.start(2200)
        else:
            self.set_expanded(False)
        self.show()
        self.raise_()

    def _compact_after_recovery(self) -> None:
        if self._last_event is not None and self._last_event.state is RuntimeState.RECOVERED:
            self.set_expanded(False)

    def _stop_current_task(self) -> None:
        if getattr(self.window.runner, "is_running", False):
            self.window.runner.stop()
        real = getattr(self.window, "execution_runner", None)
        if real is not None and getattr(real, "is_running", False):
            real.stop()
        batch = getattr(self.window, "batch_workspace", None)
        stop = getattr(batch, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
        self.user_response.emit({"action": "stop_requested"})

    def cleanup(self, *_args: Any) -> None:
        self._save_position()
        try:
            self.close()
        except RuntimeError:
            pass


def install_runtime_assistant(window: Any) -> RuntimeAssistant:
    existing = getattr(window, "_runtime_assistant", None)
    if isinstance(existing, RuntimeAssistant):
        return existing
    bridge = install_runtime_event_bridge(window)
    shadow = install_runtime_shadow_recovery(window)
    assistant = RuntimeAssistant(window)
    bridge.event_emitted.connect(assistant.present)
    shadow.event_emitted.connect(assistant.present)
    window._runtime_assistant = assistant
    window.destroyed.connect(assistant.cleanup)
    app = QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(assistant.cleanup)
    return assistant


__all__ = ["RuntimeAssistant", "install_runtime_assistant"]
