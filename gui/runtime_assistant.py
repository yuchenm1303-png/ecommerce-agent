"""Lightweight non-modal Runtime Assistant for progress and recovery telemetry."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.makro.runtime_contract import RuntimeEvent, RuntimeState
from .runtime_event_bridge import install_runtime_event_bridge


_URGENT_STATES = {
    RuntimeState.AI_ANALYZING,
    RuntimeState.RECOVERING,
    RuntimeState.WAITING_FOR_USER,
    RuntimeState.WARNING,
    RuntimeState.FAILED,
}


class RuntimeAssistant(QFrame):
    """Always-available, non-modal runtime surface."""

    user_response = Signal(object)

    _WIDTH = 430
    _COMPACT_HEIGHT = 72
    _EXPANDED_HEIGHT = 246

    def __init__(self, window: Any) -> None:
        root = window.centralWidget()
        if root is None:
            raise RuntimeError("Runtime Assistant requires a central widget")
        super().__init__(root)
        self.window = window
        self.root = root
        self._expanded = False
        self._last_event: RuntimeEvent | None = None

        self.setObjectName("runtimeAssistant")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(self._WIDTH)
        self.setFixedHeight(self._COMPACT_HEIGHT)
        self.setStyleSheet(
            """
            QFrame#runtimeAssistant {
                background-color: rgba(8, 20, 36, 224);
                border: 1px solid rgba(204, 229, 255, 48);
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
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
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
        layout.addWidget(self.detail_label)

        self.alert_label = QLabel("")
        self.alert_label.setObjectName("runtimeAssistantAlert")
        self.alert_label.setTextFormat(Qt.TextFormat.PlainText)
        self.alert_label.setWordWrap(True)
        layout.addWidget(self.alert_label)

        self.suggestion_title = QLabel("Recovery 建议")
        self.suggestion_title.setObjectName("runtimeAssistantMeta")
        layout.addWidget(self.suggestion_title)

        self.suggestion_label = QLabel("")
        self.suggestion_label.setObjectName("runtimeAssistantMeta")
        self.suggestion_label.setTextFormat(Qt.TextFormat.PlainText)
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
        self.root.installEventFilter(self)
        self._reposition()
        self.show()
        self.raise_()

        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._compact_after_recovery)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self.root and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        }:
            QTimer.singleShot(0, self._reposition)
        return False

    def _reposition(self) -> None:
        try:
            x = max(16, self.root.width() - self.width() - 24)
            self.move(x, 22)
            self.raise_()
        except RuntimeError:
            pass

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        for widget in self._expanded_widgets:
            widget.setVisible(self._expanded)
        self.setFixedHeight(self._EXPANDED_HEIGHT if self._expanded else self._COMPACT_HEIGHT)
        self._reposition()

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

    def cleanup(self) -> None:
        try:
            self.root.removeEventFilter(self)
        except RuntimeError:
            pass


def install_runtime_assistant(window: Any) -> RuntimeAssistant:
    existing = getattr(window, "_runtime_assistant", None)
    if isinstance(existing, RuntimeAssistant):
        return existing
    bridge = install_runtime_event_bridge(window)
    assistant = RuntimeAssistant(window)
    bridge.event_emitted.connect(assistant.present)
    window._runtime_assistant = assistant
    window.destroyed.connect(assistant.cleanup)
    return assistant


__all__ = ["RuntimeAssistant", "install_runtime_assistant"]
