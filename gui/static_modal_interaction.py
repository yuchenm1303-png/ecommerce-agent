from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QElapsedTimer, QObject, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QMainWindow, QWidget

from .card_details_fast import FastCardDetailController


_OPEN_MS = 210
_CLOSE_MS = 165
_FRAME_MS = 16
_OPEN_RISE_PX = 14.0
_CLOSE_DROP_PX = 10.0
_OPEN_SCALE = 0.985
_CLOSE_SCALE = 0.990
_OVERLAY_PAD = 28

_STATE_IDLE = "idle"
_STATE_OPENING = "opening"
_STATE_OPEN = "open"
_STATE_CLOSING = "closing"


class _PanelTransitionWidget(QWidget):
    """Paint one panel snapshot; never owns input or layout."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("cardDetailPanelTransition")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.hide()

        self._pixmap = QPixmap()
        self._target = QRectF()
        self._opening = True
        self._duration_ms = _OPEN_MS
        self._progress = 0.0
        self._finished: Callable[[], None] | None = None
        self._elapsed = QElapsedTimer()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

    def configure(
        self,
        pixmap: QPixmap,
        target: QRect,
        *,
        opening: bool,
        duration_ms: int,
        finished: Callable[[], None],
    ) -> None:
        self.stop()
        self._pixmap = pixmap
        self._opening = opening
        self._duration_ms = max(1, int(duration_ms))
        self._progress = 0.0
        self._finished = finished

        parent = self.parentWidget()
        if parent is None:
            raise RuntimeError("panel transition requires a QWidget parent")
        bounds = target.adjusted(
            -_OVERLAY_PAD,
            -_OVERLAY_PAD,
            _OVERLAY_PAD,
            _OVERLAY_PAD + int(max(_OPEN_RISE_PX, _CLOSE_DROP_PX)),
        ).intersected(parent.rect())
        self.setGeometry(bounds)
        local = QRect(target)
        local.translate(-bounds.left(), -bounds.top())
        self._target = QRectF(local)

    @staticmethod
    def _out_cubic(value: float) -> float:
        inv = 1.0 - value
        return 1.0 - inv * inv * inv

    @staticmethod
    def _out_quart(value: float) -> float:
        inv = 1.0 - value
        return 1.0 - inv * inv * inv * inv

    @staticmethod
    def _in_cubic(value: float) -> float:
        return value * value * value

    def start(self) -> None:
        if self._pixmap.isNull() or self._target.isEmpty():
            callback = self._finished
            self._finished = None
            if callback is not None:
                callback()
            return
        self._progress = 0.0
        self._elapsed.start()
        self.show()
        self.raise_()
        self.repaint()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._finished = None
        self.hide()

    def _tick(self) -> None:
        elapsed = self._elapsed.elapsed()
        self._progress = min(1.0, elapsed / float(self._duration_ms))
        self.update()
        if self._progress < 1.0:
            return
        self._timer.stop()
        callback = self._finished
        self._finished = None
        # Keep the final snapshot above the real UI until the callback has
        # synchronously painted the destination state. This removes the classic
        # one-frame QWidget handoff flash without touching any native window.
        if callback is not None:
            callback()
        self.hide()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if self._pixmap.isNull() or self._target.isEmpty():
            return

        progress = max(0.0, min(1.0, self._progress))
        if self._opening:
            motion = self._out_quart(progress)
            opacity = self._out_cubic(progress)
            scale = _OPEN_SCALE + (1.0 - _OPEN_SCALE) * motion
            offset_y = _OPEN_RISE_PX * (1.0 - motion)
        else:
            motion = self._in_cubic(progress)
            opacity = 1.0 - motion
            scale = 1.0 - (1.0 - _CLOSE_SCALE) * motion
            offset_y = _CLOSE_DROP_PX * motion

        target = QRectF(self._target)
        width = target.width() * scale
        height = target.height() * scale
        draw_rect = QRectF(
            target.center().x() - width * 0.5,
            target.center().y() - height * 0.5 + offset_y,
            width,
            height,
        )

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        painter.drawPixmap(draw_rect, self._pixmap, QRectF(self._pixmap.rect()))
        painter.end()


class StaticModalInteractionController(QObject):
    """Animate the existing QWidget modal without creating another native surface.

    The real blurred backdrop and scrim stay static in the existing QWidget tree.
    Only a mouse-transparent snapshot of the modal panel is animated. No main
    layout, splitter, source card, native HWND, QQuickWindow or real drawer geometry
    participates in the per-frame path.
    """

    def __init__(self, window: QMainWindow, details: FastCardDetailController) -> None:
        super().__init__(window)
        self.window = window
        self.details = details
        self.root = window.centralWidget()
        if self.root is None:
            raise RuntimeError("static modal interaction requires a central widget")

        self._passive_labels: dict[QLabel, bool] = {}
        self._state = _STATE_IDLE
        self._original_show_prepared_modal = self.details._show_prepared_modal  # noqa: SLF001
        self._original_close = self.details.close
        self._panel = _PanelTransitionWidget(self.root)

        self.details._show_prepared_modal = self._show_with_animation  # type: ignore[method-assign]  # noqa: SLF001
        self.details.close = self.request_close  # type: ignore[method-assign]
        self._rewire_close_inputs()
        self._install_card_surfaces()
        window.destroyed.connect(self.cleanup)

    @staticmethod
    def _label_is_passive(label: QLabel) -> bool:
        flags = label.textInteractionFlags()
        interactive = (
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        return not bool(flags & interactive)

    def _install_card_surfaces(self) -> None:
        for card in self.details._expandable_cards:  # noqa: SLF001
            if not isinstance(card, QFrame):
                continue
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            for label in card.findChildren(QLabel):
                if not self._label_is_passive(label):
                    continue
                previous = label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                self._passive_labels[label] = previous
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _rewire_close_inputs(self) -> None:
        try:
            self.details.close_button.clicked.disconnect(self._original_close)
        except (RuntimeError, TypeError):
            pass
        self.details.close_button.clicked.connect(self.request_close)

        try:
            self.details.scrim.clicked.disconnect(self._original_close)
        except (RuntimeError, TypeError):
            pass
        self.details.scrim.clicked.connect(self.request_close)

    def _prepare_open_frame(self, *, ratio: tuple[float, float]) -> tuple[QPixmap, QRect]:
        self.details._modal_ratio = ratio  # noqa: SLF001
        backdrop = self.details._capture_backdrop()  # noqa: SLF001
        target = self.details._drawer_rect()  # noqa: SLF001
        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)

        try:
            self.details.backdrop.setPixmap(backdrop)
            self.details.backdrop.setGeometry(self.root.rect())
            self.details.scrim.setGeometry(self.root.rect())
            self.details.drawer.setGeometry(target)
            self.details.body_layout.activate()
            if self.details.drawer.layout() is not None:
                self.details.drawer.layout().activate()
            self.details.scroll.verticalScrollBar().setValue(0)

            panel = self.details.drawer.grab()
            self.details.backdrop.show()
            self.details.backdrop.raise_()
            self.details.scrim.show()
            self.details.scrim.raise_()
            self.details.drawer.hide()
            self.details.ghost.hide()
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)
                self.root.update()

        # Paint the static blur/scrim exactly once before panel motion begins.
        # The 60 Hz hot path below therefore redraws only the small panel snapshot.
        self.root.repaint()
        return panel, target

    def _show_with_animation(self, *, ratio: tuple[float, float]) -> None:
        if self._state not in {_STATE_IDLE, _STATE_OPEN}:
            return
        if self._state == _STATE_OPEN or self.details.drawer.isVisible():
            return

        try:
            panel, target = self._prepare_open_frame(ratio=ratio)
            if panel.isNull():
                raise RuntimeError("modal panel snapshot is empty")
            self._state = _STATE_OPENING
            self._panel.configure(
                panel,
                target,
                opening=True,
                duration_ms=_OPEN_MS,
                finished=self._finish_open,
            )
            self._panel.start()
        except Exception:
            self._panel.stop()
            # _prepare_open_frame may already have exposed backdrop/scrim. Reset
            # that partial state before the direct static fallback captures again.
            try:
                self._original_close()
            except RuntimeError:
                pass
            self._state = _STATE_IDLE
            self._original_show_prepared_modal(ratio=ratio)
            self._state = _STATE_OPEN

    def _finish_open(self) -> None:
        if self._state != _STATE_OPENING:
            return
        self.details.drawer.setGeometry(self.details._drawer_rect())  # noqa: SLF001
        self.details.drawer.show()
        self.details.drawer.raise_()
        self.details.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self.details._schedule_geometry()  # noqa: SLF001
        self.root.repaint()
        self._state = _STATE_OPEN

    def request_close(self, *_args: object) -> None:
        if self._state == _STATE_OPENING:
            self._panel.stop()
            self._state = _STATE_OPENING
            self._finish_open()
        if self._state == _STATE_CLOSING:
            return

        if not self.details.drawer.isVisible() and not self.details.scrim.isVisible():
            self._state = _STATE_IDLE
            return

        if self._state not in {_STATE_OPEN, _STATE_IDLE}:
            return

        try:
            target = self.details.drawer.geometry()
            panel = self.details.drawer.grab()
            if panel.isNull():
                raise RuntimeError("modal panel snapshot is empty")

            self._state = _STATE_CLOSING
            self._panel.configure(
                panel,
                target,
                opening=False,
                duration_ms=_CLOSE_MS,
                finished=self._finish_close,
            )
            self._panel.start()
            self._panel.repaint()
            self.details.drawer.hide()
        except Exception:
            self._panel.stop()
            self._state = _STATE_IDLE
            self._original_close()

    def _finish_close(self) -> None:
        if self._state != _STATE_CLOSING:
            return
        self._original_close()
        self.root.repaint()
        self._state = _STATE_IDLE

    def cleanup(self) -> None:
        self._panel.stop()
        self._state = _STATE_IDLE

        try:
            self.details.close_button.clicked.disconnect(self.request_close)
        except (RuntimeError, TypeError):
            pass
        try:
            self.details.scrim.clicked.disconnect(self.request_close)
        except (RuntimeError, TypeError):
            pass
        try:
            self.details.close_button.clicked.connect(self._original_close)
            self.details.scrim.clicked.connect(self._original_close)
        except RuntimeError:
            pass

        try:
            self.details._show_prepared_modal = self._original_show_prepared_modal  # type: ignore[method-assign]  # noqa: SLF001
            self.details.close = self._original_close  # type: ignore[method-assign]
        except RuntimeError:
            pass

        for label, previous in tuple(self._passive_labels.items()):
            try:
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, previous)
            except RuntimeError:
                pass
        self._passive_labels.clear()


def install_static_modal_interaction(
    window: QMainWindow,
    details: FastCardDetailController,
) -> StaticModalInteractionController:
    controller = StaticModalInteractionController(window, details)
    window._static_modal_interaction = controller  # type: ignore[attr-defined]
    return controller
