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
from PySide6.QtQuick import (
    QQuickItem,
    QQuickPaintedItem,
    QQuickWindow,
    QSGSimpleTextureNode,
    QSGTexture,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QFrame,
    QGraphicsEffect,
    QMainWindow,
    QWidget,
)

from .native_visual_style import _CardScaleEffect


_NORMAL_SCALE = 1.00
_HOVER_SCALE = 1.02
_ACTIVE_SCALE = 1.00
_NORMAL_ALPHA = 64.0
_HOVER_ALPHA = 102.0
_ACTIVE_ALPHA = 102.0
_TRANSITION_MS = 300
_GLASS_RADIUS = 6.0
_PARK_COORD = -100000.0
_WARM_SWAP_WATCHDOG_MS = 80

_DIRTY_EVENTS = {
    QEvent.Type.Paint,
    QEvent.Type.UpdateRequest,
    QEvent.Type.LayoutRequest,
    QEvent.Type.Resize,
    QEvent.Type.Show,
    QEvent.Type.Hide,
    QEvent.Type.StyleChange,
    QEvent.Type.FontChange,
    QEvent.Type.PaletteChange,
    QEvent.Type.EnabledChange,
    QEvent.Type.FocusIn,
    QEvent.Type.FocusOut,
    QEvent.Type.DynamicPropertyChange,
    QEvent.Type.ChildAdded,
    QEvent.Type.ChildRemoved,
}
_GEOMETRY_EVENTS = {
    QEvent.Type.Move,
    QEvent.Type.Resize,
    QEvent.Type.LayoutRequest,
    QEvent.Type.Show,
    QEvent.Type.Hide,
}


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
    """Legacy source composite with a screen-paint gate for static Quick motion."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._source_visible = True

    def set_source_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible != self._source_visible:
            self._source_visible = visible
            self.update()

    def draw(self, painter: QPainter) -> None:  # type: ignore[override]
        if self._source_visible:
            self.drawSource(painter)


class _TextureItem(QQuickItem):
    """Resident scene-graph texture. QImage uploads happen only on cache refresh."""

    def __init__(self, parent: QQuickItem) -> None:
        super().__init__(parent)
        self._image = QImage()
        self._offset = QPoint()
        self._revision = 0
        self._rendered_revision = -1
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self.setTransformOrigin(QQuickItem.TransformOrigin.Center)
        self.setVisible(True)

    @property
    def ready(self) -> bool:
        return not self._image.isNull()

    def set_snapshot(self, image: QImage, offset: QPoint) -> None:
        self._image = QImage(image)
        self._offset = QPoint(offset)
        self._revision += 1
        self.update()

    def clear(self) -> None:
        self._image = QImage()
        self._offset = QPoint()
        self._revision += 1
        self.update()

    def updatePaintNode(self, old_node, _data):  # type: ignore[override]
        if self._image.isNull():
            return None

        node = old_node if isinstance(old_node, QSGSimpleTextureNode) else QSGSimpleTextureNode()
        if old_node is None or self._rendered_revision != self._revision:
            quick = self.window()
            if quick is None:
                return node
            texture = quick.createTextureFromImage(self._image)
            if texture is None:
                return node
            previous = node.texture() if old_node is not None else None
            node.setTexture(texture)
            node.setOwnsTexture(True)
            node.setFiltering(QSGTexture.Filtering.Linear)
            self._rendered_revision = self._revision
            if previous is not None and previous is not texture:
                try:
                    previous.deleteLater()
                except RuntimeError:
                    pass

        dpr = max(1.0, float(self._image.devicePixelRatio()))
        node.setRect(
            QRectF(
                float(self._offset.x()),
                float(self._offset.y()),
                float(self._image.width()) / dpr,
                float(self._image.height()) / dpr,
            )
        )
        return node


class _TintItem(QQuickPaintedItem):
    """One persistent rounded tint texture; scale/opacity are scene-graph properties."""

    def __init__(self, parent: QQuickItem) -> None:
        super().__init__(parent)
        self.setTransformOrigin(QQuickItem.TransformOrigin.Center)
        self.setAntialiasing(True)
        self.setVisible(True)

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
    texture: _TextureItem
    group: QParallelAnimationGroup
    texture_scale_anim: QPropertyAnimation
    tint_scale_anim: QPropertyAnimation
    tint_alpha_anim: QPropertyAnimation
    cached: bool = False
    active: bool = False
    target_scale: float = _NORMAL_SCALE
    target_alpha: float = _NORMAL_ALPHA


class StaticQuickCardCompositor(QObject):
    """Drift-off compositor with resident card textures and event-driven targets.

    Static mode is deliberately isolated from the established drift-on lane.
    Before static takeover, currently visible cards are rasterized once while the
    legacy QWidget path is still live, uploaded into persistent Quick scene-graph
    nodes, then parked offscreen. Hover/press never calls sourcePixmap(), never
    uploads a new image and never creates animation objects: it only moves the
    already-resident layer onscreen and retargets persistent scale/opacity
    animations. Widget content changes invalidate only the owning card cache and
    are coalesced outside the pointer event that caused them.

    Drift-on restores the exact legacy card lane and exact _CardScaleEffect. This
    class does not modify native_background, PresentationClock, wallpaper drift or
    the legacy NekroCardInteractionController implementation.
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
        self._capturing: set[QFrame] = set()
        self._dirty_cards: set[QFrame] = set()
        self._dirty_posted = False
        self._geometry_posted = False
        self._warm_generation = 0
        self._warm_queue: list[QFrame] = []
        self._awaiting_warm_swap: int | None = None
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
        self.quick.frameSwapped.connect(self._on_quick_frame_swapped)

        for frame, state in self.card_fx.states.items():
            self._layers[frame] = self._make_layer(frame, state.surface)

        if self._app is not None:
            self._app.installEventFilter(self)
        if self.toggle is not None:
            self.toggle.toggled.connect(self._on_drift_changed)
        for area in window.findChildren(QAbstractScrollArea):
            area.verticalScrollBar().valueChanged.connect(self._schedule_geometry_refresh)
            area.horizontalScrollBar().valueChanged.connect(self._schedule_geometry_refresh)

        window.destroyed.connect(self.cleanup)
        self._on_drift_changed(bool(self.clock.background_drift_enabled))

    def _make_layer(self, frame: QFrame, surface: Any) -> _Layer:
        clip = QQuickItem(self.quick.contentItem())
        clip.setClip(True)
        clip.setVisible(True)
        clip.setX(_PARK_COORD)
        clip.setY(_PARK_COORD)
        clip.setWidth(1.0)
        clip.setHeight(1.0)

        tint = _TintItem(clip)
        tint.setZ(0.0)
        tint.setOpacity(_NORMAL_ALPHA / 255.0)
        texture = _TextureItem(clip)
        texture.setZ(1.0)

        group = QParallelAnimationGroup(self)
        texture_scale_anim = QPropertyAnimation(texture, b"scale", group)
        tint_scale_anim = QPropertyAnimation(tint, b"scale", group)
        tint_alpha_anim = QPropertyAnimation(tint, b"opacity", group)
        for animation in (texture_scale_anim, tint_scale_anim, tint_alpha_anim):
            animation.setDuration(_TRANSITION_MS)
            animation.setEasingCurve(self._ease)
            group.addAnimation(animation)

        layer = _Layer(
            frame=frame,
            surface=surface,
            clip=clip,
            tint=tint,
            texture=texture,
            group=group,
            texture_scale_anim=texture_scale_anim,
            tint_scale_anim=tint_scale_anim,
            tint_alpha_anim=tint_alpha_anim,
        )
        group.finished.connect(lambda layer=layer: self._animation_finished(layer))
        return layer

    def _fit_quick(self, *_args: object) -> None:
        self.quick.setGeometry(0, 0, max(1, self.owner.width()), max(1, self.owner.height()))

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

    def _capture_layer(self, layer: _Layer) -> bool:
        if layer.frame in self._capturing:
            return layer.cached
        effect = self._effect(layer)
        if not isinstance(effect, QGraphicsEffect):
            return False

        self._capturing.add(layer.frame)
        try:
            offset = QPoint()
            pixmap = effect.sourcePixmap(
                Qt.CoordinateSystem.LogicalCoordinates,
                offset,
                QGraphicsEffect.PixmapPadMode.NoPad,
            )
            if pixmap.isNull():
                return False
            image = pixmap.toImage()
            image.setDevicePixelRatio(pixmap.devicePixelRatio())
            layer.texture.set_snapshot(image, offset)
            layer.cached = True
            self._prepare_resident_geometry(layer)
            return True
        except RuntimeError:
            return False
        finally:
            self._capturing.discard(layer.frame)

    def _snapshot_geometry(self, layer: _Layer) -> dict[str, float | bool] | None:
        try:
            return self.background.card_model._snapshot(layer.frame)  # noqa: SLF001
        except RuntimeError:
            return None

    def _prepare_resident_geometry(self, layer: _Layer) -> None:
        geometry = self._snapshot_geometry(layer)
        if geometry is None:
            return
        card_w = max(1.0, float(geometry.get("cardW", 1.0)))
        card_h = max(1.0, float(geometry.get("cardH", 1.0)))
        layer.clip.setWidth(card_w * 1.08)
        layer.clip.setHeight(card_h * 1.08)
        layer.tint.setX(0.0)
        layer.tint.setY(0.0)
        layer.tint.setWidth(card_w)
        layer.tint.setHeight(card_h)
        layer.texture.setX(0.0)
        layer.texture.setY(0.0)
        layer.texture.setWidth(card_w)
        layer.texture.setHeight(card_h)
        self._park(layer)

    def _position_active(self, layer: _Layer) -> bool:
        geometry = self._snapshot_geometry(layer)
        if geometry is None or not bool(geometry.get("cardVisible", False)):
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
        for item in (layer.tint, layer.texture):
            item.setX(local_x)
            item.setY(local_y)
            item.setWidth(card_w)
            item.setHeight(card_h)
        return True

    @staticmethod
    def _park(layer: _Layer) -> None:
        layer.clip.setX(_PARK_COORD)
        layer.clip.setY(_PARK_COORD)

    def _reset_legacy_state(self) -> None:
        self.card_fx._moving_frames.clear()  # noqa: SLF001
        self.card_fx.hovered = None
        self.card_fx.pressed = None
        self.card_fx._left_down = False  # noqa: SLF001
        self.card_fx._none_samples = 0  # noqa: SLF001
        self.card_fx._next_motion_s = 0.0  # noqa: SLF001
        for state in self.card_fx.states.values():
            try:
                self.card_fx._set_content_frozen(state, False)  # noqa: SLF001
                state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)
            except RuntimeError:
                state.current_scale = state.from_scale = state.target_scale = _NORMAL_SCALE
                state.current_alpha = state.from_alpha = state.target_alpha = _NORMAL_ALPHA
                state.moving = False

    def _visible_layers(self) -> list[_Layer]:
        layers: list[_Layer] = []
        for layer in self._layers.values():
            try:
                if layer.frame.isVisibleTo(self.window) and layer.frame.width() > 0 and layer.frame.height() > 0:
                    layers.append(layer)
            except RuntimeError:
                continue
        return layers

    def _start_prewarm(self) -> None:
        if self._cleaned or bool(self.clock.background_drift_enabled):
            return
        if self._static_enabled:
            return
        self._warm_generation += 1
        generation = self._warm_generation
        self._awaiting_warm_swap = None
        self._warm_queue = []
        for layer in self._visible_layers():
            layer.cached = False
            self._warm_queue.append(layer.frame)
        QTimer.singleShot(0, lambda generation=generation: self._prewarm_next(generation))

    def _prewarm_next(self, generation: int) -> None:
        if (
            self._cleaned
            or generation != self._warm_generation
            or bool(self.clock.background_drift_enabled)
        ):
            return
        if self._warm_queue:
            frame = self._warm_queue.pop(0)
            layer = self._layers.get(frame)
            if layer is not None:
                self._capture_layer(layer)
            QTimer.singleShot(0, lambda generation=generation: self._prewarm_next(generation))
            return

        required = self._visible_layers()
        if any(not layer.cached or not layer.texture.ready for layer in required):
            return

        self._fit_quick()
        self.quick.show()
        _raise_native_child(self.quick)
        for layer in required:
            self._prepare_resident_geometry(layer)
        self._awaiting_warm_swap = generation
        try:
            self.quick.requestUpdate()
        except RuntimeError:
            pass
        QTimer.singleShot(
            _WARM_SWAP_WATCHDOG_MS,
            lambda generation=generation: self._finish_prewarm(generation),
        )

    def _on_quick_frame_swapped(self) -> None:
        generation = self._awaiting_warm_swap
        if generation is None:
            return
        self._awaiting_warm_swap = None
        QTimer.singleShot(0, lambda generation=generation: self._finish_prewarm(generation))

    def _finish_prewarm(self, generation: int) -> None:
        if (
            self._cleaned
            or generation != self._warm_generation
            or bool(self.clock.background_drift_enabled)
            or self._static_enabled
        ):
            return
        self._awaiting_warm_swap = None
        required = self._visible_layers()
        if any(not layer.cached or not layer.texture.ready for layer in required):
            return

        self._reset_legacy_state()
        for layer in self._layers.values():
            self._set_static_effect(layer)
            layer.target_scale = _NORMAL_SCALE
            layer.target_alpha = _NORMAL_ALPHA
            layer.texture.setScale(_NORMAL_SCALE)
            layer.tint.setScale(_NORMAL_SCALE)
            layer.tint.setOpacity(_NORMAL_ALPHA / 255.0)
            layer.active = False
            self._park(layer)

        self.clock._clear_widget_lane()  # noqa: SLF001
        self.clock.card_fx = self._noop_card_lane
        self._hovered = None
        self._pressed = None
        self._static_enabled = True
        self._schedule_geometry_refresh()
        QTimer.singleShot(0, self._sync_hover)

    def _activate_layer(self, layer: _Layer) -> bool:
        if layer.active:
            return True
        if not layer.cached or not layer.texture.ready:
            return False
        if not self._position_active(layer):
            return False
        effect = self._effect(layer)
        if not isinstance(effect, _StaticScaleEffect):
            return False

        layer.texture.setScale(_NORMAL_SCALE)
        layer.tint.setScale(_NORMAL_SCALE)
        layer.tint.setOpacity(_NORMAL_ALPHA / 255.0)
        effect.set_source_visible(False)
        self.background.set_card_presentation(
            layer.frame,
            scale=_NORMAL_SCALE,
            alpha=0.0,
        )
        layer.surface._surface_scale = _NORMAL_SCALE  # type: ignore[attr-defined]
        layer.surface._overlay_alpha = _NORMAL_ALPHA  # type: ignore[attr-defined]
        layer.active = True
        return True

    def _deactivate_layer(self, layer: _Layer) -> None:
        layer.group.stop()
        effect = self._effect(layer)
        if isinstance(effect, _StaticScaleEffect):
            effect.set_source_visible(True)
            effect.set_scale(_NORMAL_SCALE)
        self.background.set_card_presentation(
            layer.frame,
            scale=_NORMAL_SCALE,
            alpha=_NORMAL_ALPHA,
        )
        layer.surface._surface_scale = _NORMAL_SCALE  # type: ignore[attr-defined]
        layer.surface._overlay_alpha = _NORMAL_ALPHA  # type: ignore[attr-defined]
        layer.texture.setScale(_NORMAL_SCALE)
        layer.tint.setScale(_NORMAL_SCALE)
        layer.tint.setOpacity(_NORMAL_ALPHA / 255.0)
        layer.target_scale = _NORMAL_SCALE
        layer.target_alpha = _NORMAL_ALPHA
        layer.active = False
        self._park(layer)

    def _retarget(self, layer: _Layer, scale: float, alpha: float) -> None:
        if not self._static_enabled:
            return
        scale = float(scale)
        alpha = float(alpha)
        if not layer.active and not self._activate_layer(layer):
            self._mark_dirty(layer.frame)
            return

        if (
            abs(scale - layer.target_scale) <= 1e-5
            and abs(alpha - layer.target_alpha) <= 0.05
            and layer.group.state() != QParallelAnimationGroup.State.Stopped
        ):
            return

        layer.group.stop()
        start_scale = float(layer.texture.scale())
        start_alpha = float(layer.tint.opacity()) * 255.0
        layer.target_scale = scale
        layer.target_alpha = alpha

        specs = (
            (layer.texture_scale_anim, start_scale, scale),
            (layer.tint_scale_anim, start_scale, scale),
            (layer.tint_alpha_anim, start_alpha / 255.0, alpha / 255.0),
        )
        for animation, start, end in specs:
            animation.setStartValue(float(start))
            animation.setEndValue(float(end))
        layer.group.start()

    def _animation_finished(self, layer: _Layer) -> None:
        if not self._static_enabled or not layer.active:
            return
        layer.texture.setScale(layer.target_scale)
        layer.tint.setScale(layer.target_scale)
        layer.tint.setOpacity(layer.target_alpha / 255.0)
        if (
            abs(layer.target_scale - _NORMAL_SCALE) <= 1e-5
            and abs(layer.target_alpha - _NORMAL_ALPHA) <= 0.05
        ):
            self._deactivate_layer(layer)

    def _hover_scale(self, frame: QFrame) -> float:
        return float(self.card_fx._hover_scale_for(frame))  # noqa: SLF001

    def _nearest_card(self, widget: QWidget | None) -> QFrame | None:
        if widget is None:
            return None
        try:
            card = self.card_fx._nearest_card(widget)  # noqa: SLF001
        except RuntimeError:
            return None
        return card if card in self._layers else None

    def _card_at(self, global_pos: QPoint) -> QFrame | None:
        try:
            card = self.card_fx._card_at_global(global_pos)  # noqa: SLF001
        except RuntimeError:
            return None
        return card if card in self._layers else None

    def _set_hover(self, frame: QFrame | None) -> None:
        if self._pressed is not None or frame is self._hovered:
            return
        previous = self._hovered
        self._hovered = frame
        if previous is not None:
            layer = self._layers.get(previous)
            if layer is not None:
                self._retarget(layer, _NORMAL_SCALE, _NORMAL_ALPHA)
        if frame is not None:
            layer = self._layers.get(frame)
            if layer is not None:
                self._retarget(layer, self._hover_scale(frame), _HOVER_ALPHA)

    def _begin_press(self, frame: QFrame | None) -> None:
        previous = self._hovered
        self._hovered = frame
        self._pressed = frame
        if previous is not None and previous is not frame:
            layer = self._layers.get(previous)
            if layer is not None:
                self._retarget(layer, _NORMAL_SCALE, _NORMAL_ALPHA)
        if frame is not None:
            layer = self._layers.get(frame)
            if layer is not None:
                self._retarget(layer, _ACTIVE_SCALE, _ACTIVE_ALPHA)
        elif previous is not None:
            layer = self._layers.get(previous)
            if layer is not None:
                self._retarget(layer, _NORMAL_SCALE, _NORMAL_ALPHA)

    def _end_press(self, global_pos: QPoint) -> None:
        previous = self._pressed
        current = self._card_at(global_pos)
        self._pressed = None
        self._hovered = current
        if previous is not None:
            layer = self._layers.get(previous)
            if layer is not None:
                if previous is current:
                    self._retarget(layer, self._hover_scale(previous), _HOVER_ALPHA)
                else:
                    self._retarget(layer, _NORMAL_SCALE, _NORMAL_ALPHA)
        if current is not None and current is not previous:
            layer = self._layers.get(current)
            if layer is not None:
                self._retarget(layer, self._hover_scale(current), _HOVER_ALPHA)

    def _sync_hover(self) -> None:
        if self._can_present():
            self._set_hover(self._card_at(QPoint(QCursor.pos())))

    def _can_present(self) -> bool:
        try:
            return bool(
                self._static_enabled
                and not self._cleaned
                and not self.clock._holds  # noqa: SLF001
                and not bool(getattr(self.card_fx, "_suspended", False))
                and self.window.isVisible()
                and not self.window.isMinimized()
            )
        except RuntimeError:
            return False

    @staticmethod
    def _global_pos(event: QEvent) -> QPoint:
        for name in ("globalPosition", "globalPos"):
            getter = getattr(event, name, None)
            if callable(getter):
                try:
                    point = getter()
                    to_point = getattr(point, "toPoint", None)
                    return QPoint(to_point() if callable(to_point) else point)
                except (RuntimeError, TypeError):
                    continue
        return QPoint(QCursor.pos())

    @staticmethod
    def _is_left(event: QEvent) -> bool:
        getter = getattr(event, "button", None)
        if not callable(getter):
            return True
        try:
            return getter() == Qt.MouseButton.LeftButton
        except RuntimeError:
            return False

    def _ancestor_cards(self, frame: QFrame) -> list[QFrame]:
        result = [frame]
        parent = frame.parentWidget()
        while parent is not None:
            if isinstance(parent, QFrame) and parent in self._layers:
                result.append(parent)
            if parent is self.window:
                break
            parent = parent.parentWidget()
        return result

    def _mark_dirty(self, frame: QFrame) -> None:
        if not self._static_enabled:
            return
        for card in self._ancestor_cards(frame):
            if card not in self._capturing:
                self._dirty_cards.add(card)
        if self._dirty_cards and not self._dirty_posted:
            self._dirty_posted = True
            QTimer.singleShot(0, self._flush_dirty)

    def _mark_visible_dirty(self) -> None:
        if not self._static_enabled:
            return
        for layer in self._visible_layers():
            self._dirty_cards.add(layer.frame)
        if self._dirty_cards and not self._dirty_posted:
            self._dirty_posted = True
            QTimer.singleShot(0, self._flush_dirty)

    def _flush_dirty(self) -> None:
        self._dirty_posted = False
        if not self._static_enabled or self._cleaned:
            self._dirty_cards.clear()
            return
        dirty = list(self._dirty_cards)
        self._dirty_cards.clear()
        for frame in dirty:
            layer = self._layers.get(frame)
            if layer is None:
                continue
            try:
                visible = layer.frame.isVisibleTo(self.window)
            except RuntimeError:
                visible = False
            if visible:
                self._capture_layer(layer)
        try:
            self.quick.requestUpdate()
        except RuntimeError:
            pass

    def _schedule_geometry_refresh(self, *_args: object) -> None:
        if not self._static_enabled or self._geometry_posted:
            return
        self._geometry_posted = True
        QTimer.singleShot(0, self._flush_geometry)

    def _flush_geometry(self) -> None:
        self._geometry_posted = False
        if not self._static_enabled:
            return
        for layer in self._layers.values():
            if layer.active:
                if not self._position_active(layer):
                    self._deactivate_layer(layer)
            elif layer.cached:
                self._prepare_resident_geometry(layer)

    def _handoff_all_to_widgets(self) -> None:
        if not self._static_enabled:
            return
        self._hovered = None
        self._pressed = None
        for layer in self._layers.values():
            if layer.active:
                self._deactivate_layer(layer)

    def _leave_static(self) -> None:
        self._warm_generation += 1
        self._awaiting_warm_swap = None
        self._warm_queue.clear()
        self._dirty_cards.clear()
        self._dirty_posted = False
        self._geometry_posted = False

        if not self._static_enabled:
            self.quick.hide()
            return

        self._handoff_all_to_widgets()
        self._static_enabled = False
        for layer in self._layers.values():
            self._set_legacy_effect(layer)
            layer.cached = False
            layer.texture.clear()
            self._park(layer)
        self.clock._clear_widget_lane()  # noqa: SLF001
        self.clock.card_fx = self._legacy_clock_card_fx
        self._reset_legacy_state()
        self.quick.hide()

    def _on_drift_changed(self, enabled: bool) -> None:
        if bool(enabled):
            self._leave_static()
        else:
            self._start_prewarm()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if self._cleaned:
            return False

        event_type = event.type()
        if watched is self.window:
            if event_type == QEvent.Type.WindowDeactivate:
                self._handoff_all_to_widgets()
            elif event_type in _GEOMETRY_EVENTS:
                self._schedule_geometry_refresh()
                if event_type in {QEvent.Type.Resize, QEvent.Type.LayoutRequest}:
                    self._mark_visible_dirty()
            return False

        if not isinstance(watched, QWidget):
            return False

        card = self._nearest_card(watched)
        if self._static_enabled and card is not None and card not in self._capturing:
            if event_type in _DIRTY_EVENTS:
                self._mark_dirty(card)
            if event_type in _GEOMETRY_EVENTS:
                self._schedule_geometry_refresh()

        if not self._can_present() or card is None:
            return False

        if event_type == QEvent.Type.Enter:
            self._set_hover(card)
        elif event_type == QEvent.Type.Leave:
            if self._hovered is not None:
                QTimer.singleShot(0, self._sync_hover)
        elif event_type == QEvent.Type.MouseButtonPress and self._is_left(event):
            self._begin_press(card or self._card_at(self._global_pos(event)))
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
            try:
                self._app.removeEventFilter(self)
            except RuntimeError:
                pass
        try:
            self.quick.frameSwapped.disconnect(self._on_quick_frame_swapped)
        except (RuntimeError, TypeError):
            pass
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
