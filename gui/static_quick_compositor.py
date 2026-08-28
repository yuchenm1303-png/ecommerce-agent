from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QCursor, QImage, QPainter
from PySide6.QtQuick import QQuickItem, QQuickPaintedItem, QQuickWindow
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsEffect, QMainWindow, QWidget

from .native_visual_style import _CardScaleEffect


_NORMAL_SCALE = 1.00
_HOVER_SCALE = 1.02
_ACTIVE_SCALE = 1.00
_NORMAL_ALPHA = 64.0
_HOVER_ALPHA = 102.0
_ACTIVE_ALPHA = 102.0
_TRANSITION_MS = 300
_GLASS_RADIUS = 6.0


def _css_ease() -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(0.25, 0.10),
        QPointF(0.25, 1.00),
        QPointF(1.00, 1.00),
    )
    return curve


def _raise_native_child(window: QQuickWindow) -> None:
    if sys.platform != "win32":
        raise_window = getattr(window, "raise_", None)
        if callable(raise_window):
            raise_window()
        return
    hwnd = int(window.winId())
    if not hwnd:
        return
    flags = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
    ctypes.windll.user32.SetWindowPos(
        ctypes.c_void_p(hwnd), ctypes.c_void_p(0), 0, 0, 0, 0, flags
    )


class _StaticScaleEffect(_CardScaleEffect):
    """Legacy card effect with a paint gate used only by the static compositor."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._source_visible = True

    def set_source_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible != self._source_visible:
            self._source_visible = visible
            self.update()

    def capture_source(self) -> tuple[QImage, QPoint] | None:
        offset = QPoint()
        pixmap = self.sourcePixmap(
            Qt.CoordinateSystem.LogicalCoordinates,
            offset,
            QGraphicsEffect.PixmapPadMode.NoPad,
        )
        if pixmap.isNull():
            return None
        return pixmap.toImage(), QPoint(offset)

    def draw(self, painter: QPainter) -> None:  # type: ignore[override]
        if self._source_visible:
            super().draw(painter)


class _SnapshotItem(QQuickPaintedItem):
    def __init__(self, parent: QQuickItem) -> None:
        super().__init__(parent)
        self._image = QImage()
        self._offset = QPoint()
        self.setTransformOrigin(QQuickItem.TransformOrigin.Center)
        self.setAntialiasing(True)
        self.setVisible(False)

    def set_snapshot(self, image: QImage, offset: QPoint) -> None:
        self._image = QImage(image)
        self._offset = QPoint(offset)
        self.update()

    def clear(self) -> None:
        self._image = QImage()
        self.update()

    def paint(self, painter: QPainter) -> None:  # type: ignore[override]
        if self._image.isNull():
            return
        dpr = max(1.0, float(self._image.devicePixelRatio()))
        target = QRectF(
            float(self._offset.x()),
            float(self._offset.y()),
            float(self._image.width()) / dpr,
            float(self._image.height()) / dpr,
        )
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(target, self._image)


class _TintItem(QQuickPaintedItem):
    def __init__(self, parent: QQuickItem) -> None:
        super().__init__(parent)
        self.setTransformOrigin(QQuickItem.TransformOrigin.Center)
        self.setAntialiasing(True)
        self.setVisible(False)

    def paint(self, painter: QPainter) -> None:  # type: ignore[override]
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0))
        painter.drawRoundedRect(self.boundingRect(), _GLASS_RADIUS, _GLASS_RADIUS)


class _NoopCardLane:
    def presentation_tick(self, *_args: object, **_kwargs: object) -> None:
        pass


@dataclass(slots=True)
class _Layer:
    frame: QFrame
    surface: Any
    clip: QQuickItem
    tint: _TintItem
    snapshot: _SnapshotItem
    current_scale: float = _NORMAL_SCALE
    current_alpha: float = _NORMAL_ALPHA
    target_scale: float = _NORMAL_SCALE
    target_alpha: float = _NORMAL_ALPHA
    animation: QParallelAnimationGroup | None = None
    generation: int = 0
    active: bool = False


class StaticQuickCardCompositor(QObject):
    """Browser-style card transition compositor for drift-off mode only.

    Drift-on mode restores the exact legacy card lane and exact legacy
    _CardScaleEffect. The background renderer, PresentationClock implementation,
    wallpaper drift and visual constants are never modified by this class.
    """

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = visual.background
        self.owner = self.background.quick_window
        self.clock = window._presentation_clock  # type: ignore[attr-defined]
        self.card_fx = window._nekro_card_fx  # type: ignore[attr-defined]
        self.toggle = getattr(window, "_background_drift_switch", None)
        self._app = QApplication.instance()
        self._legacy_clock_card_fx = self.clock.card_fx
        self._noop_card_lane = _NoopCardLane()
        self._layers: dict[QFrame, _Layer] = {}
        self._hovered: QFrame | None = None
        self._pressed: QFrame | None = None
        self._static_enabled = False
        self._cleaned = False
        self._ease = _css_ease()

        if not isinstance(self.owner, QQuickWindow):
            raise RuntimeError("static compositor requires the existing QQuickWindow")

        self.quick = QQuickWindow(self.owner)
        self.quick.setColor(QColor(0, 0, 0, 0))
        self.quick.setFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.quick.setPersistentGraphics(True)
        self.quick.setPersistentSceneGraph(True)
        self._fit_quick()
        self.owner.widthChanged.connect(self._fit_quick)
        self.owner.heightChanged.connect(self._fit_quick)

        for frame, state in self.card_fx.states.items():
            self._layers[frame] = self._make_layer(frame, state.surface)

        if self._app is not None:
            self._app.installEventFilter(self)
        if self.toggle is not None:
            self.toggle.toggled.connect(self._on_drift_changed)
        window.destroyed.connect(self.cleanup)
        self._on_drift_changed(bool(self.clock.background_drift_enabled))

    def _make_layer(self, frame: QFrame, surface: Any) -> _Layer:
        clip = QQuickItem(self.quick.contentItem())
        clip.setClip(True)
        clip.setVisible(False)
        tint = _TintItem(clip)
        tint.setZ(0.0)
        snapshot = _SnapshotItem(clip)
        snapshot.setZ(1.0)
        return _Layer(frame, surface, clip, tint, snapshot)

    def _fit_quick(self, *_args: object) -> None:
        self.quick.setGeometry(0, 0, max(1, self.owner.width()), max(1, self.owner.height()))

    def _position(self, layer: _Layer) -> bool:
        geometry = self.background.card_model._snapshot(layer.frame)  # noqa: SLF001
        if not geometry["cardVisible"]:
            return False
        clip_x = float(geometry["clipX"])
        clip_y = float(geometry["clipY"])
        clip_w = float(geometry["clipW"])
        clip_h = float(geometry["clipH"])
        card_w = float(geometry["cardW"])
        card_h = float(geometry["cardH"])
        if min(clip_w, clip_h, card_w, card_h) <= 0.0:
            return False

        layer.clip.setX(clip_x)
        layer.clip.setY(clip_y)
        layer.clip.setWidth(clip_w)
        layer.clip.setHeight(clip_h)
        local_x = float(geometry["cardX"]) - clip_x
        local_y = float(geometry["cardY"]) - clip_y
        for item in (layer.tint, layer.snapshot):
            item.setX(local_x)
            item.setY(local_y)
            item.setWidth(card_w)
            item.setHeight(card_h)
        layer.clip.setVisible(True)
        return True

    def _effect(self, layer: _Layer) -> Any:
        return layer.surface._scale_effect  # type: ignore[attr-defined]

    def _set_static_effect(self, layer: _Layer) -> _StaticScaleEffect:
        current = self._effect(layer)
        if isinstance(current, _StaticScaleEffect):
            return current
        effect = _StaticScaleEffect(layer.frame)
        layer.frame.setGraphicsEffect(effect)
        layer.surface._scale_effect = effect  # type: ignore[attr-defined]
        layer.surface._surface_scale = _NORMAL_SCALE  # type: ignore[attr-defined]
        layer.surface._overlay_alpha = _NORMAL_ALPHA  # type: ignore[attr-defined]
        return effect

    def _set_legacy_effect(self, layer: _Layer) -> None:
        current = self._effect(layer)
        if type(current) is _CardScaleEffect:
            return
        effect = _CardScaleEffect(layer.frame)
        layer.frame.setGraphicsEffect(effect)
        layer.surface._scale_effect = effect  # type: ignore[attr-defined]
        layer.surface._surface_scale = _NORMAL_SCALE  # type: ignore[attr-defined]
        layer.surface._overlay_alpha = _NORMAL_ALPHA  # type: ignore[attr-defined]

    def _commit(self, layer: _Layer, scale: float, alpha: float) -> None:
        self.background.set_card_presentation(layer.frame, scale=scale, alpha=alpha)
        self._effect(layer).set_scale(scale)
        layer.surface._surface_scale = scale  # type: ignore[attr-defined]
        layer.surface._overlay_alpha = alpha  # type: ignore[attr-defined]
        layer.current_scale = layer.target_scale = scale
        layer.current_alpha = layer.target_alpha = alpha

    def _stop(self, layer: _Layer) -> None:
        if layer.animation is not None:
            layer.animation.stop()
            layer.animation.deleteLater()
            layer.animation = None

    def _hide(self, layer: _Layer) -> None:
        layer.active = False
        layer.clip.setVisible(False)
        layer.tint.setVisible(False)
        layer.snapshot.setVisible(False)
        layer.snapshot.clear()

    def _normalize(self, layer: _Layer) -> None:
        layer.generation += 1
        self._stop(layer)
        effect = self._effect(layer)
        if isinstance(effect, _StaticScaleEffect):
            effect.set_source_visible(True)
        self._commit(layer, _NORMAL_SCALE, _NORMAL_ALPHA)
        self._hide(layer)

    def _reset_legacy_state(self) -> None:
        self.card_fx._moving_frames.clear()  # noqa: SLF001
        self.card_fx.hovered = None
        self.card_fx.pressed = None
        self.card_fx._left_down = False  # noqa: SLF001
        self.card_fx._none_samples = 0  # noqa: SLF001
        self.card_fx._next_motion_s = 0.0  # noqa: SLF001
        for state in self.card_fx.states.values():
            state.current_scale = state.from_scale = state.target_scale = _NORMAL_SCALE
            state.current_alpha = state.from_alpha = state.target_alpha = _NORMAL_ALPHA
            state.moving = False

    def _animate(self, frame: QFrame | None, scale: float, alpha: float) -> None:
        if not self._static_enabled or frame is None:
            return
        layer = self._layers.get(frame)
        if layer is None:
            return

        if layer.active:
            start_scale = float(layer.snapshot.scale())
            start_alpha = float(layer.tint.opacity()) * 255.0
            self._stop(layer)
        else:
            if (
                abs(layer.current_scale - scale) <= 1e-5
                and abs(layer.current_alpha - alpha) <= 0.05
            ):
                return
            effect = self._effect(layer)
            if not isinstance(effect, _StaticScaleEffect):
                return
            captured = effect.capture_source()
            if captured is None or not self._position(layer):
                self._commit(layer, scale, alpha)
                return
            image, offset = captured
            layer.snapshot.set_snapshot(image, offset)
            start_scale = layer.current_scale
            start_alpha = layer.current_alpha
            layer.snapshot.setScale(start_scale)
            layer.tint.setScale(start_scale)
            layer.tint.setOpacity(start_alpha / 255.0)
            layer.tint.setVisible(True)
            layer.snapshot.setVisible(True)
            layer.active = True
            effect.set_source_visible(False)
            self.background.set_card_presentation(
                layer.frame, scale=_NORMAL_SCALE, alpha=0.0
            )

        layer.target_scale = float(scale)
        layer.target_alpha = float(alpha)
        layer.generation += 1
        generation = layer.generation

        group = QParallelAnimationGroup(self)
        specs = (
            (layer.snapshot, b"scale", start_scale, scale),
            (layer.tint, b"scale", start_scale, scale),
            (layer.tint, b"opacity", start_alpha / 255.0, alpha / 255.0),
        )
        for target, prop, start, end in specs:
            animation = QPropertyAnimation(target, prop, group)
            animation.setDuration(_TRANSITION_MS)
            animation.setStartValue(float(start))
            animation.setEndValue(float(end))
            animation.setEasingCurve(self._ease)
            group.addAnimation(animation)
        group.finished.connect(
            lambda layer=layer, generation=generation: self._finish(layer, generation)
        )
        layer.animation = group
        group.start()

    def _finish(self, layer: _Layer, generation: int) -> None:
        if not self._static_enabled or generation != layer.generation:
            return
        layer.animation = None
        self._commit(layer, layer.target_scale, layer.target_alpha)
        effect = self._effect(layer)
        if isinstance(effect, _StaticScaleEffect):
            effect.set_source_visible(True)
        self._hide(layer)

    def _hover_scale(self, frame: QFrame) -> float:
        return float(self.card_fx._hover_scale_for(frame))  # noqa: SLF001

    def _nearest_card(self, widget: QWidget | None) -> QFrame | None:
        card = self.card_fx._nearest_card(widget)  # noqa: SLF001
        return card if card in self._layers else None

    def _card_at(self, global_pos: QPoint) -> QFrame | None:
        card = self.card_fx._card_at_global(global_pos)  # noqa: SLF001
        return card if card in self._layers else None

    def _set_hover(self, frame: QFrame | None) -> None:
        if self._pressed is not None or frame is self._hovered:
            return
        previous = self._hovered
        self._hovered = frame
        if previous is not None:
            self._animate(previous, _NORMAL_SCALE, _NORMAL_ALPHA)
        if frame is not None:
            self._animate(frame, self._hover_scale(frame), _HOVER_ALPHA)

    def _begin_press(self, frame: QFrame | None) -> None:
        previous = self._hovered
        self._hovered = frame
        self._pressed = frame
        if previous is not None and previous is not frame:
            self._animate(previous, _NORMAL_SCALE, _NORMAL_ALPHA)
        if frame is not None:
            self._animate(frame, _ACTIVE_SCALE, _ACTIVE_ALPHA)
        elif previous is not None:
            self._animate(previous, _NORMAL_SCALE, _NORMAL_ALPHA)

    def _end_press(self, global_pos: QPoint) -> None:
        previous = self._pressed
        current = self._card_at(global_pos)
        self._pressed = None
        self._hovered = current
        if previous is not None:
            if previous is current:
                self._animate(previous, self._hover_scale(previous), _HOVER_ALPHA)
            else:
                self._animate(previous, _NORMAL_SCALE, _NORMAL_ALPHA)
        if current is not None and current is not previous:
            self._animate(current, self._hover_scale(current), _HOVER_ALPHA)

    def _sync_hover(self) -> None:
        if self._can_present():
            self._set_hover(self._card_at(QPoint(QCursor.pos())))

    def _can_present(self) -> bool:
        return bool(
            self._static_enabled
            and not self._cleaned
            and not self.clock._holds  # noqa: SLF001
            and self.window.isVisible()
            and not self.window.isMinimized()
        )

    @staticmethod
    def _global_pos(event: QEvent) -> QPoint:
        for name in ("globalPosition", "globalPos"):
            getter = getattr(event, name, None)
            if callable(getter):
                point = getter()
                to_point = getattr(point, "toPoint", None)
                return QPoint(to_point() if callable(to_point) else point)
        return QPoint(QCursor.pos())

    @staticmethod
    def _is_left(event: QEvent) -> bool:
        getter = getattr(event, "button", None)
        return not callable(getter) or getter() == Qt.MouseButton.LeftButton

    def _enter_static(self) -> None:
        if self._static_enabled:
            return
        self._static_enabled = True
        self._hovered = self._pressed = None
        self._reset_legacy_state()
        for layer in self._layers.values():
            self._normalize(layer)
            self._set_static_effect(layer)
        self.clock._clear_widget_lane()  # noqa: SLF001
        self.clock.card_fx = self._noop_card_lane
        self._fit_quick()
        self.quick.show()
        _raise_native_child(self.quick)
        QTimer.singleShot(0, self._sync_hover)

    def _leave_static(self) -> None:
        if not self._static_enabled:
            return
        self._static_enabled = False
        self._hovered = self._pressed = None
        for layer in self._layers.values():
            self._normalize(layer)
            self._set_legacy_effect(layer)
        self.quick.hide()
        self.clock._clear_widget_lane()  # noqa: SLF001
        self.clock.card_fx = self._legacy_clock_card_fx
        self._reset_legacy_state()

    def _on_drift_changed(self, enabled: bool) -> None:
        if enabled:
            self._leave_static()
        else:
            self._enter_static()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not self._can_present() or not isinstance(watched, QWidget):
            return False
        event_type = event.type()
        if event_type == QEvent.Type.Enter:
            card = self._nearest_card(watched)
            if card is not None:
                self._set_hover(card)
        elif event_type == QEvent.Type.Leave:
            if self._hovered is not None:
                QTimer.singleShot(0, self._sync_hover)
        elif event_type == QEvent.Type.MouseButtonPress and self._is_left(event):
            card = self._nearest_card(watched) or self._card_at(self._global_pos(event))
            self._begin_press(card)
        elif event_type == QEvent.Type.MouseButtonRelease and self._is_left(event):
            self._end_press(self._global_pos(event))
        return False

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._leave_static()
        self._cleaned = True
        if self.toggle is not None:
            try:
                self.toggle.toggled.disconnect(self._on_drift_changed)
            except (RuntimeError, TypeError):
                pass
        if self._app is not None:
            self._app.removeEventFilter(self)
        self.quick.close()
        self.quick.deleteLater()
        self._layers.clear()


def install_static_quick_card_compositor(
    window: QMainWindow,
    visual: Any,
) -> StaticQuickCardCompositor | None:
    existing = getattr(window, "_static_quick_card_compositor", None)
    if isinstance(existing, StaticQuickCardCompositor):
        return existing
    if not hasattr(window, "_presentation_clock") or not hasattr(window, "_nekro_card_fx"):
        return None
    compositor = StaticQuickCardCompositor(window, visual)
    window._static_quick_card_compositor = compositor  # type: ignore[attr-defined]
    return compositor


__all__ = ["StaticQuickCardCompositor", "install_static_quick_card_compositor"]
