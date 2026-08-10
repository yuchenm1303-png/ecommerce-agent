from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QElapsedTimer, QObject, QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QWidget


_FRAME_MS = 8
_DEFAULT_DURATION_MS = 168
_TRAVEL_PX = 9


def _smootherstep(value: float) -> float:
    p = max(0.0, min(1.0, float(value)))
    return p * p * p * (p * (p * 6.0 - 15.0) + 10.0)


class ClipSheetMotion(QObject):
    """Animate one absolute-positioned sheet without touching application layout.

    The live content widget is always kept at its final width/height.  Only a
    lightweight parent viewport changes geometry, so QLabel/QTableWidget layout,
    word wrapping, splitters and the main QWidget tree never see intermediate
    animation sizes.
    """

    opened = Signal()
    closed = Signal()

    def __init__(
        self,
        root: QWidget,
        content: QWidget,
        rect_provider: Callable[[], QRect],
        *,
        edge: str,
        duration_ms: int = _DEFAULT_DURATION_MS,
    ) -> None:
        super().__init__(root)
        if edge not in {"top", "bottom", "right"}:
            raise ValueError(f"unsupported sheet edge: {edge}")

        self.root = root
        self.content = content
        self.rect_provider = rect_provider
        self.edge = edge
        self.duration_ms = max(120, int(duration_ms))
        self.progress = 0.0
        self.target = 0.0
        self.animating = False
        self._start_progress = 0.0
        self._final_rect = QRect()

        self.viewport = QFrame(root)
        self.viewport.setObjectName("overlaySheetViewport")
        self.viewport.setStyleSheet("QFrame#overlaySheetViewport { background: transparent; border: 0; }")
        self.viewport.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.viewport.hide()

        self.content.setParent(self.viewport)
        self.content.hide()

        self._elapsed = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def is_open(self) -> bool:
        return self.progress >= 0.999 and self.viewport.isVisible()

    def _prepare_final_geometry(self) -> None:
        rect = QRect(self.rect_provider())
        if rect.width() <= 0 or rect.height() <= 0:
            rect = QRect(0, 0, 1, 1)
        self._final_rect = rect
        self.content.resize(rect.size())
        layout = self.content.layout()
        if layout is not None:
            layout.activate()

    def _apply(self, raw_progress: float) -> None:
        self.progress = max(0.0, min(1.0, float(raw_progress)))
        eased = _smootherstep(self.progress)
        final = self._final_rect

        if self.edge == "right":
            width = max(1, int(round(final.width() * eased)))
            x = final.x() + final.width() - width
            self.viewport.setGeometry(x, final.y(), width, final.height())
            self.content.move(-(final.width() - width) + int(round(_TRAVEL_PX * (1.0 - eased))), 0)
        elif self.edge == "bottom":
            height = max(1, int(round(final.height() * eased)))
            y = final.y() + final.height() - height
            self.viewport.setGeometry(final.x(), y, final.width(), height)
            self.content.move(0, -(final.height() - height) + int(round(_TRAVEL_PX * (1.0 - eased))))
        else:
            height = max(1, int(round(final.height() * eased)))
            self.viewport.setGeometry(final.x(), final.y(), final.width(), height)
            self.content.move(0, -int(round(_TRAVEL_PX * (1.0 - eased))))

        self.viewport.raise_()

    def _start(self, target: float) -> None:
        target = 1.0 if target >= 0.5 else 0.0
        if target > 0.0:
            self._prepare_final_geometry()
            self.content.show()
            self.viewport.show()
            self.viewport.raise_()

        if abs(self.progress - target) < 0.001:
            self.target = target
            self._finish()
            return

        self.target = target
        self._start_progress = self.progress
        self.animating = True
        self.viewport.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._elapsed.restart()
        if not self._timer.isActive():
            self._timer.start()

    def open(self) -> None:
        self._start(1.0)

    def close(self) -> None:
        if not self.viewport.isVisible() and self.progress <= 0.001:
            return
        self._start(0.0)

    def toggle(self, opened: bool) -> None:
        self.open() if opened else self.close()

    def _tick(self) -> None:
        elapsed = max(0, self._elapsed.elapsed())
        phase = min(1.0, elapsed / float(self.duration_ms))
        value = self._start_progress + (self.target - self._start_progress) * phase
        self._apply(value)
        if phase >= 1.0:
            self._finish()

    def _finish(self) -> None:
        self._timer.stop()
        self.animating = False
        self.progress = self.target

        if self.target >= 0.5:
            self._prepare_final_geometry()
            self.viewport.setGeometry(self._final_rect)
            self.content.move(0, 0)
            self.content.show()
            self.viewport.show()
            self.viewport.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.viewport.raise_()
            self.opened.emit()
        else:
            self.viewport.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.content.hide()
            self.viewport.hide()
            self.closed.emit()

    def sync_geometry(self) -> None:
        if not self.viewport.isVisible():
            return
        self._prepare_final_geometry()
        self._apply(self.progress)
        if self.is_open and not self.animating:
            self.viewport.setGeometry(self._final_rect)
            self.content.move(0, 0)

    def stop(self) -> None:
        self._timer.stop()
        self.animating = False

    def cleanup(self) -> None:
        self.stop()
        self.content.hide()
        self.viewport.hide()
