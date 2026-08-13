"""QWidget-owned glass card shells for the formal GUI.

Quick remains the wallpaper/parallax owner. Each moving card owns a child QWidget
backdrop that samples the already pre-blurred wallpaper and paints the local glass
shell. Card position therefore comes from the QWidget hierarchy itself; scrolling
never waits for QWidget -> Python geometry publication -> Quick card delegates.
"""

from __future__ import annotations

from types import MethodType

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QMainWindow, QWidget

from .native_background import _OVERSCAN
from .native_visual_style import NativeGlassProxy


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_LOCAL_CARD_STYLE = """
QFrame#glassCard,
QFrame#heroCard,
QFrame#statusCard,
QFrame#microCard {
    background: transparent;
    border: 0;
    border-radius: 6px;
}
"""
_GLASS_RADIUS = 6.0


def _style_local_card(frame: QFrame) -> None:
    """Keep the frame transparent; its child backdrop owns the complete shell."""

    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    frame.setStyleSheet(_LOCAL_CARD_STYLE)


def _clear_quick_card_model(background) -> None:  # noqa: ANN001
    """Remove moving card delegates/masks from Quick without touching wallpaper."""

    model = getattr(background, "card_model", None)
    if model is None:
        return

    model.beginResetModel()
    try:
        model.cards.clear()
        model._rows.clear()  # noqa: SLF001
        model._states.clear()  # noqa: SLF001
    finally:
        model.endResetModel()


class _LocalGlassBackdrop(QWidget):
    """One card-local blurred wallpaper sample plus the translucent glass tint."""

    def __init__(self, frame: QFrame, controller: "_WidgetCardGlassController") -> None:
        super().__init__(frame)
        self.frame = frame
        self.controller = controller
        self.setObjectName("widgetOwnedGlassBackdrop")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        frame.installEventFilter(self)
        self.sync_geometry()

    def sync_geometry(self) -> None:
        self.setGeometry(self.frame.rect())
        self.lower()
        self.show()
        self.update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.frame and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            self.sync_geometry()
        return False

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        cover = self.controller.cover()
        if cover.isNull() or self.width() <= 0 or self.height() <= 0:
            return

        quick = self.controller.quick
        if quick is None:
            return

        try:
            local = quick.mapFromGlobal(self.frame.mapToGlobal(QPoint(0, 0)))
            image_x = float(quick.property("imageX"))
            image_y = float(quick.property("imageY"))
        except (RuntimeError, TypeError, ValueError):
            return

        source_rect = QRectF(
            float(local.x()) - image_x,
            float(local.y()) - image_y,
            float(self.width()),
            float(self.height()),
        )
        target_rect = QRectF(self.rect())

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        path = QPainterPath()
        path.addRoundedRect(target_rect, _GLASS_RADIUS, _GLASS_RADIUS)
        painter.setClipPath(path)
        painter.drawPixmap(target_rect, cover, source_rect)

        alpha = self.controller.alpha_for(self.frame)
        painter.fillRect(target_rect, QColor(0, 0, 0, int(round(alpha))))
        painter.end()


class _WidgetCardGlassController(QObject):
    """Shared cache/repaint controller for all QWidget-owned card backdrops."""

    def __init__(self, window: QMainWindow, visual, background) -> None:  # noqa: ANN001
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = background
        self.quick = getattr(background, "quick_window", None)
        blur_path = getattr(background, "_blur_path", None)
        self._source = QPixmap(str(blur_path)) if blur_path is not None else QPixmap()
        if self._source.isNull():
            raise RuntimeError("widget card glass could not load the pre-blurred wallpaper")

        self._cover = QPixmap()
        self._cover_key: tuple[int, int] | None = None
        self._backdrops: dict[QFrame, _LocalGlassBackdrop] = {}
        self._scroll_bars: set[QObject] = set()

        if self.quick is not None:
            self.quick.widthChanged.connect(self._invalidate_cover)
            self.quick.heightChanged.connect(self._invalidate_cover)
            for signal_name in ("offsetXChanged", "offsetYChanged"):
                signal = getattr(self.quick, signal_name, None)
                if signal is not None and hasattr(signal, "connect"):
                    signal.connect(self.update_visible)

    def _invalidate_cover(self, *_args: object) -> None:
        self._cover_key = None
        self._cover = QPixmap()
        self.update_visible()

    def cover(self) -> QPixmap:
        quick = self.quick
        if quick is None:
            return QPixmap()
        try:
            width = max(1, int(quick.width()))
            height = max(1, int(quick.height()))
        except RuntimeError:
            return QPixmap()

        key = (width, height)
        if self._cover_key == key and not self._cover.isNull():
            return self._cover

        target_w = max(1, round(width * _OVERSCAN))
        target_h = max(1, round(height * _OVERSCAN))
        scaled = self._source.scaled(
            QSize(target_w, target_h),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - target_w) // 2)
        y = max(0, (scaled.height() - target_h) // 2)
        self._cover = scaled.copy(x, y, target_w, target_h)
        self._cover_key = key
        return self._cover

    def alpha_for(self, frame: QFrame) -> float:
        proxy = getattr(self.visual, "_glass", {}).get(frame)
        try:
            return max(0.0, min(255.0, float(proxy.overlay_alpha)))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return 64.0

    def add_frame(self, frame: QFrame) -> None:
        if frame in self._backdrops:
            return
        _style_local_card(frame)
        backdrop = _LocalGlassBackdrop(frame, self)
        self._backdrops[frame] = backdrop

        proxy = getattr(self.visual, "_glass", {}).get(frame)
        if proxy is not None:
            original = proxy.set_interaction

            def set_interaction(surface, *, scale: float, overlay_alpha: float) -> None:  # noqa: ANN001
                original(scale=scale, overlay_alpha=overlay_alpha)
                current = self._backdrops.get(frame)
                if current is not None:
                    current.update()

            proxy.set_interaction = MethodType(set_interaction, proxy)

    def attach_scroll_areas(self) -> None:
        for area in self.window.findChildren(QAbstractScrollArea):
            for bar in (area.verticalScrollBar(), area.horizontalScrollBar()):
                if bar in self._scroll_bars:
                    continue
                bar.valueChanged.connect(self.update_visible)
                self._scroll_bars.add(bar)

    def update_visible(self, *_args: object) -> None:
        for backdrop in tuple(self._backdrops.values()):
            try:
                if backdrop.isVisibleTo(self.window):
                    backdrop.update()
            except RuntimeError:
                continue


def install_widget_card_rendering(window: QMainWindow, visual) -> None:  # noqa: ANN001
    """Move card glass into each owning QWidget while retaining Quick wallpaper."""

    if bool(getattr(visual, "_widget_card_rendering", False)):
        return

    background = getattr(visual, "background", None)
    if background is None:
        raise RuntimeError("widget card rendering requires the native Quick background")

    original_schedule = getattr(background, "schedule_mask_update", None)
    _clear_quick_card_model(background)

    # Stop already-wired scroll/resize signals from spending time rebuilding an
    # empty full-window mask. Future callers see the no-op below.
    if callable(original_schedule):
        for area in window.findChildren(QAbstractScrollArea):
            for bar in (area.verticalScrollBar(), area.horizontalScrollBar()):
                try:
                    bar.valueChanged.disconnect(original_schedule)
                except (RuntimeError, TypeError):
                    pass
        quick = getattr(background, "quick_window", None)
        if quick is not None:
            for signal in (quick.widthChanged, quick.heightChanged):
                try:
                    signal.disconnect(original_schedule)
                except (RuntimeError, TypeError):
                    pass

    timer = getattr(background, "_geometry_timer", None)
    if timer is not None:
        try:
            timer.stop()
        except RuntimeError:
            pass

    def ignore_card_geometry_updates(_background, *_args: object) -> None:  # noqa: ANN001
        return None

    background.schedule_mask_update = MethodType(ignore_card_geometry_updates, background)

    controller = _WidgetCardGlassController(window, visual, background)
    visual._widget_card_glass_controller = controller  # type: ignore[attr-defined]

    for frame in tuple(getattr(visual, "_glass", {})):
        if isinstance(frame, QFrame) and frame.objectName() in _GLASS_NAMES:
            controller.add_frame(frame)
    controller.attach_scroll_areas()

    def refresh_widget_cards(owner) -> int:  # noqa: ANN001
        new_frames = [
            frame
            for frame in owner.window.findChildren(QFrame)
            if frame.objectName() in _GLASS_NAMES and frame not in owner._glass  # noqa: SLF001
        ]
        for frame in new_frames:
            owner._glass[frame] = NativeGlassProxy(frame, owner.background)  # noqa: SLF001
            controller.add_frame(frame)
        controller.attach_scroll_areas()
        controller.update_visible()
        return len(new_frames)

    visual.refresh_glass_frames = MethodType(refresh_widget_cards, visual)
    visual._widget_card_rendering = True  # type: ignore[attr-defined]
