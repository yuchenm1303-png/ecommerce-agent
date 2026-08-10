"""Continuous smooth wheel scrolling without a QApplication-wide event filter."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractScrollArea, QWidget


class SmoothScroller(QObject):
    _STEP_MS = 16
    _EASE = 0.18

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._animations: dict[int, list] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(self._STEP_MS)
        self._timer.timeout.connect(self._tick)

    def push(self, bar, delta: int) -> None:
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
    """Filter Wheel only on real scroll areas/viewports, never on QApplication."""

    PIXELS_PER_NOTCH = 36

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scroller = SmoothScroller(self)
        self._areas: dict[QObject, QAbstractScrollArea] = {}

    def install(self, root: QWidget) -> None:
        for area in root.findChildren(QAbstractScrollArea):
            self._attach(area)

    def _attach(self, area: QAbstractScrollArea) -> None:
        for watched in (area, area.viewport()):
            if watched in self._areas:
                continue
            self._areas[watched] = area
            watched.installEventFilter(self)

    def eventFilter(self, watched: QObject, event) -> bool:  # noqa: ANN001, N802
        if event.type() != QEvent.Type.Wheel:
            return False
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False

        area = self._areas.get(watched)
        if area is None:
            return False
        delta = event.angleDelta().y()
        if delta == 0:
            return False

        bar = area.verticalScrollBar()
        if not bar.isVisible() or bar.maximum() <= bar.minimum():
            return False

        self._scroller.push(
            bar,
            -round(delta / 120 * self.PIXELS_PER_NOTCH),
        )
        return True

    def cleanup(self) -> None:
        self._scroller._timer.stop()
        for watched in tuple(self._areas):
            try:
                watched.removeEventFilter(self)
            except RuntimeError:
                pass
        self._areas.clear()
