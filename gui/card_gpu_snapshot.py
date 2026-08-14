from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QFrame, QGraphicsEffect, QMainWindow, QWidget


_POOL_SIZE = 2
_MAX_SCALE = 1.04
_PAD_PX = 2


class _CardPaintGate(QGraphicsEffect):
    """Hide the live QWidget subtree while its GPU snapshot is visible.

    This effect never asks Qt for a source pixmap and never transforms pixels. It is
    enabled only during a card transition, so steady-state widgets stay on Qt's
    normal backing-store path with no graphics effect installed.
    """

    def draw(self, painter: QPainter) -> None:  # type: ignore[override]
        _ = painter
        return


@dataclass(slots=True)
class _SnapshotState:
    frame: QFrame
    pixmap: QPixmap
    gate: _CardPaintGate
    scale: float


class _CardSnapshotWidget(QOpenGLWidget):
    """Small transparent OpenGL surface for one transforming card snapshot."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        fmt.setDepthBufferSize(0)
        fmt.setStencilBufferSize(0)
        fmt.setSamples(0)
        self.setFormat(fmt)

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)

        self.host = host
        self.state: _SnapshotState | None = None
        self._base_local = QRectF()
        self._clip_local = QRectF()
        self.hide()

    def _frame_rect_in_host(self, frame: QFrame) -> QRectF | None:
        if not frame.isVisibleTo(self.host) or frame.width() <= 0 or frame.height() <= 0:
            return None
        try:
            top_left = frame.mapTo(self.host, QPoint(0, 0))
        except RuntimeError:
            return None
        return QRectF(
            float(top_left.x()),
            float(top_left.y()),
            float(frame.width()),
            float(frame.height()),
        )

    def _clip_rect_in_host(self, frame: QFrame) -> QRectF:
        clip = QRectF(0.0, 0.0, float(self.host.width()), float(self.host.height()))
        ancestor = frame.parentWidget()
        while ancestor is not None:
            if not ancestor.isVisibleTo(self.host):
                return QRectF()
            try:
                top_left = ancestor.mapTo(self.host, QPoint(0, 0))
            except RuntimeError:
                return QRectF()
            ancestor_rect = QRectF(
                float(top_left.x()),
                float(top_left.y()),
                float(ancestor.width()),
                float(ancestor.height()),
            )
            clip = clip.intersected(ancestor_rect)
            if clip.isEmpty() or ancestor is self.host:
                break
            ancestor = ancestor.parentWidget()
        return clip

    def sync_geometry(self) -> bool:
        state = self.state
        if state is None:
            return False
        base = self._frame_rect_in_host(state.frame)
        if base is None:
            return False

        clip = self._clip_rect_in_host(state.frame)
        pad_x = int(math.ceil(base.width() * (_MAX_SCALE - 1.0) * 0.5)) + _PAD_PX
        pad_y = int(math.ceil(base.height() * (_MAX_SCALE - 1.0) * 0.5)) + _PAD_PX
        surface_rect = base.adjusted(-pad_x, -pad_y, pad_x, pad_y).toAlignedRect()
        if surface_rect.width() <= 0 or surface_rect.height() <= 0:
            return False

        if self.geometry() != surface_rect:
            self.setGeometry(surface_rect)

        origin_x = float(surface_rect.x())
        origin_y = float(surface_rect.y())
        self._base_local = base.translated(-origin_x, -origin_y)
        self._clip_local = clip.translated(-origin_x, -origin_y)
        return True

    def attach(self, state: _SnapshotState) -> None:
        self.state = state
        if not self.sync_geometry():
            self.state = None
            return
        self.show()
        self.raise_()
        self.update()

    def detach(self) -> None:
        self.state = None
        self.hide()
        self.update()

    def set_scale(self, scale: float) -> None:
        state = self.state
        if state is None:
            return
        scale = max(0.96, min(_MAX_SCALE, float(scale)))
        geometry_changed = self.sync_geometry()
        if abs(state.scale - scale) < 0.0001 and geometry_changed:
            self.update()
            return
        if abs(state.scale - scale) < 0.0001:
            return
        state.scale = scale
        self.update()

    def paintGL(self) -> None:  # noqa: N802
        context = self.context()
        if context is not None:
            functions = context.functions()
            functions.glClearColor(0.0, 0.0, 0.0, 0.0)
            functions.glClear(0x00004000)  # GL_COLOR_BUFFER_BIT

        state = self.state
        if state is None or state.pixmap.isNull() or self._base_local.isEmpty():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if not self._clip_local.isEmpty():
            painter.setClipRect(self._clip_local)

        base = self._base_local
        center = base.center()
        width = base.width() * state.scale
        height = base.height() * state.scale
        dest = QRectF(
            center.x() - width * 0.5,
            center.y() - height * 0.5,
            width,
            height,
        )
        painter.drawPixmap(dest, state.pixmap, QRectF(state.pixmap.rect()))
        painter.end()


class CardGpuSnapshotPool(QObject):
    """Two-slot GPU bridge matching the card controller's motion concurrency cap."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        central = window.centralWidget()
        if central is None:
            raise RuntimeError("Card GPU snapshot surface requires a central widget")
        self.central = central
        self._slots = [_CardSnapshotWidget(central) for _ in range(_POOL_SIZE)]
        self._active: dict[QFrame, _CardSnapshotWidget] = {}
        QTimer.singleShot(0, self._prime)

    def _prime(self) -> None:
        # Create the OpenGL backing surfaces after the native shell is shown so the
        # first real hover does not pay context creation cost. Empty surfaces are
        # fully transparent and are hidden again on the next event-loop turn.
        for index, slot in enumerate(self._slots):
            slot.setGeometry(-2 - index, -2, 1, 1)
            slot.show()
            slot.update()
        QTimer.singleShot(0, self._finish_prime)

    def _finish_prime(self) -> None:
        for slot in self._slots:
            if slot.state is None:
                slot.hide()

    def _raise_visual_overlays(self) -> None:
        effects = getattr(self.window, "_nekro_effects", None)
        if isinstance(effects, QWidget):
            effects.raise_()

    def _free_slot(self) -> _CardSnapshotWidget | None:
        slot = next((candidate for candidate in self._slots if candidate.state is None), None)
        if slot is not None:
            return slot
        # The card controller settles back to two motions after each state change,
        # but a rapid A -> B -> C hover can request C synchronously before it has
        # retired A. Reuse the oldest slot instead of allocating a third FBO.
        oldest = next(iter(self._active), None)
        if oldest is None:
            return None
        self.release(oldest)
        return next((candidate for candidate in self._slots if candidate.state is None), None)

    def capture(self, frame: QFrame, *, scale: float) -> bool:
        self.release(frame)
        if not frame.isVisibleTo(self.window) or frame.width() <= 0 or frame.height() <= 0:
            return False
        try:
            if frame.graphicsEffect() is not None:
                return False
        except RuntimeError:
            return False

        slot = self._free_slot()
        if slot is None:
            return False

        try:
            pixmap = frame.grab()
        except RuntimeError:
            return False
        if pixmap.isNull():
            return False

        gate = _CardPaintGate(frame)
        state = _SnapshotState(
            frame=frame,
            pixmap=pixmap,
            gate=gate,
            scale=max(0.96, min(_MAX_SCALE, float(scale))),
        )
        slot.attach(state)
        if slot.state is None:
            gate.deleteLater()
            return False

        frame.setGraphicsEffect(gate)
        self._active[frame] = slot
        slot.raise_()
        self._raise_visual_overlays()
        frame.update()
        return True

    def set_scale(self, frame: QFrame, scale: float) -> None:
        slot = self._active.get(frame)
        if slot is None:
            return
        slot.set_scale(scale)
        slot.raise_()
        self._raise_visual_overlays()

    def release(self, frame: QFrame) -> None:
        slot = self._active.pop(frame, None)
        if slot is None:
            return
        state = slot.state
        if state is not None:
            detached = False
            try:
                if frame.graphicsEffect() is state.gate:
                    frame.setGraphicsEffect(None)
                    detached = True
            except RuntimeError:
                pass
            if not detached:
                try:
                    state.gate.deleteLater()
                except RuntimeError:
                    pass
        slot.detach()
        try:
            frame.update()
        except RuntimeError:
            pass

    def release_all(self) -> None:
        for frame in tuple(self._active):
            self.release(frame)

    def cleanup(self) -> None:
        self.release_all()
        for slot in self._slots:
            slot.hide()
            slot.deleteLater()
        self._slots.clear()


__all__ = ["CardGpuSnapshotPool"]
