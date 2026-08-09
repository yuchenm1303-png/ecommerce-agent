"""Continuous smooth wheel scrolling for the dev GUI.

PySide6 does not expose ``QAbstractScrollArea.setVerticalScrollMode()`` on
``QScrollArea`` / ``QPlainTextEdit``, and plain per-pixel ``setValue`` still
steps once per wheel notch. So this module glides the scrollbar toward the
wheel-driven target with a short ease-out animation: every notch becomes a
smooth motion, and rapid notches compound into a continuous flow.

Wheel events over widgets that have no scrollable ancestor (spin boxes, plain
buttons, splitter handles, ...) are left untouched.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractScrollArea, QApplication


class SmoothScroller(QObject):
    """Animate scrollbar moves so wheel notches glide instead of jump."""

    _STEP_MS = 16  # ~60 fps while an animation is in flight
    _EASE = 0.18  # fraction of the remaining distance covered per tick

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._animations: dict[int, list] = {}  # id(bar) -> [bar, target]
        self._timer = QTimer(self)
        self._timer.setInterval(self._STEP_MS)
        self._timer.timeout.connect(self._tick)

    def push(self, bar, delta: int) -> None:
        """Add a pixel offset to the scrollbar's glide target."""
        entry = self._animations.get(id(bar))
        if entry is None:
            entry = self._animations[id(bar)] = [bar, float(bar.value())]
        entry[1] += delta
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        for key, (bar, target) in list(self._animations.items()):
            try:
                current = bar.value()
            except RuntimeError:
                del self._animations[key]
                continue
            diff = target - current
            if abs(diff) < 0.5:
                bar.setValue(round(target))
                del self._animations[key]
            else:
                bar.setValue(current + round(diff * self._EASE))
        if not self._animations:
            self._timer.stop()


class SmoothWheelFilter(QObject):
    """Consume wheel events and animate the area under the cursor."""

    PIXELS_PER_NOTCH = 36

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scroller = SmoothScroller(self)

    def eventFilter(self, watched, event) -> bool:
        del watched  # the filter is global; the target is found by position
        if event.type() != QEvent.Wheel:
            return False
        # Keep Ctrl+wheel (e.g. text zoom) on the original widget.
        if event.modifiers() & Qt.ControlModifier:
            return False

        delta = event.angleDelta().y()
        if delta == 0:
            return False

        cursor_widget = QApplication.widgetAt(event.globalPosition().toPoint())
        node = cursor_widget
        while node is not None:
            # Only real scroll areas own a vertical scrollbar; QSpinBox and
            # friends must keep their native wheel behavior.
            if isinstance(node, QAbstractScrollArea):
                bar = node.verticalScrollBar()
                if bar.isVisible() and bar.maximum() > bar.minimum():
                    self._scroller.push(
                        bar, -round(delta / 120 * self.PIXELS_PER_NOTCH)
                    )
                    return True
            node = node.parentWidget()
        return False
