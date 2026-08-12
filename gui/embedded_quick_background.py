from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QFrame, QMainWindow, QWidget

from .native_background import (
    GlassCardModel,
    NativeQuickBackground,
    _GEOMETRY_SYNC_MS,
    _GLASS_NAMES,
    _POINTER_SAMPLE_MS,
    _decode_wallpaper,
    _qml_source as _native_qml_source,
)


def _embedded_qml_source() -> str:
    """Reuse the proven scene verbatim, changing only its native Window root to an Item."""

    source = _native_qml_source()
    source = source.replace("import QtQuick.Window\n", "", 1)
    native_root = 'Window {\n    id: root\n    visible: false\n    color: "#17263a"'
    embedded_root = 'Rectangle {\n    id: root\n    color: "#17263a"'
    if native_root not in source:
        raise RuntimeError("Native Quick scene root contract changed")
    return source.replace(native_root, embedded_root, 1)


class _EmbeddedQuickSurface(QObject):
    """Compatibility facade over QQuickWidget without creating a second native surface."""

    widthChanged = Signal()
    heightChanged = Signal()
    frameSwapped = Signal()
    animationRunningChanged = Signal()

    def __init__(
        self,
        host: QQuickWidget,
        root: QObject,
        overlay: QMainWindow,
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self.host = host
        self.root = root
        self.overlay = overlay
        self._last_width = int(host.width())
        self._last_height = int(host.height())
        self._external_filters: list[QObject] = []

        host.installEventFilter(self)
        overlay.installEventFilter(self)

        internal = host.quickWindow()
        self._internal_window = internal
        if internal is not None:
            try:
                internal.frameSwapped.connect(self.frameSwapped.emit)
            except (RuntimeError, TypeError):
                pass

        animation_signal = getattr(root, "animationRunningChanged", None)
        if animation_signal is not None and hasattr(animation_signal, "connect"):
            try:
                animation_signal.connect(self.animationRunningChanged.emit)
            except (RuntimeError, TypeError):
                pass

    # Existing presentation controllers install filters on the old Quick owner.
    # Forward host/top-level events through the same public facade so transition,
    # minimize and restore code can stay visually unchanged during this trial.
    def installEventFilter(self, filter_obj: QObject) -> None:  # noqa: N802
        if filter_obj not in self._external_filters:
            self._external_filters.append(filter_obj)

    def removeEventFilter(self, filter_obj: QObject) -> None:  # noqa: N802
        try:
            self._external_filters.remove(filter_obj)
        except ValueError:
            pass

    def _forward_event(self, event: QEvent) -> None:
        for filter_obj in tuple(self._external_filters):
            try:
                filter_obj.eventFilter(self, event)
            except RuntimeError:
                try:
                    self._external_filters.remove(filter_obj)
                except ValueError:
                    pass

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.host and event.type() == QEvent.Type.Resize:
            width = int(self.host.width())
            height = int(self.host.height())
            if width != self._last_width:
                self._last_width = width
                self.widthChanged.emit()
            if height != self._last_height:
                self._last_height = height
                self.heightChanged.emit()
        if watched is self.host or watched is self.overlay:
            self._forward_event(event)
        return False

    def setProperty(self, name: str, value: object) -> bool:  # noqa: N802
        return bool(self.root.setProperty(name, value))

    def property(self, name: str):  # noqa: ANN201
        return self.root.property(name)

    def width(self) -> int:
        return int(self.host.width())

    def height(self) -> int:
        return int(self.host.height())

    def x(self) -> int:
        try:
            return int(self.host.mapToGlobal(QPoint(0, 0)).x())
        except RuntimeError:
            return 0

    def y(self) -> int:
        try:
            return int(self.host.mapToGlobal(QPoint(0, 0)).y())
        except RuntimeError:
            return 0

    def mapFromGlobal(self, point):  # noqa: ANN001, N802
        return self.host.mapFromGlobal(point)

    def isVisible(self) -> bool:  # noqa: N802
        return bool(self.host.isVisible())

    def isExposed(self) -> bool:  # noqa: N802
        try:
            handle = self.overlay.windowHandle()
            return bool(
                self.host.isVisible()
                and self.overlay.isVisible()
                and (handle is None or handle.isExposed())
            )
        except RuntimeError:
            return False

    def windowState(self):  # noqa: ANN201, N802
        return self.overlay.windowState()

    def update(self) -> None:
        self.host.update()

    def grabWindow(self) -> QImage:  # noqa: N802
        return self.host.grabFramebuffer()

    def setPersistentGraphics(self, enabled: bool) -> None:  # noqa: N802
        internal = self._internal_window
        if internal is not None:
            try:
                internal.setPersistentGraphics(bool(enabled))
            except RuntimeError:
                pass

    def setPersistentSceneGraph(self, enabled: bool) -> None:  # noqa: N802
        internal = self._internal_window
        if internal is not None:
            try:
                internal.setPersistentSceneGraph(bool(enabled))
            except RuntimeError:
                pass

    def releaseResources(self) -> None:  # noqa: N802
        internal = self._internal_window
        if internal is not None:
            try:
                internal.releaseResources()
            except RuntimeError:
                pass

    def hide(self) -> None:
        self.host.hide()

    def show(self) -> None:
        self.host.show()


class EmbeddedQuickBackground(NativeQuickBackground):
    """The proven Fuji/glass Quick scene rendered inside the QWidget top-level."""

    composition_domain = "widget"

    def __init__(self, overlay: QMainWindow) -> None:
        # Do not call NativeQuickBackground.__init__: it creates the native
        # QQuickWindow that this architecture trial intentionally eliminates.
        QObject.__init__(self, overlay)
        self.overlay = overlay
        self.central = overlay.centralWidget()
        if self.central is None:
            raise RuntimeError("Embedded Quick background requires a central QWidget")

        # NativeVisualStyleController prepared the overlay for the former native
        # Quick owner. Restore an ordinary single QWidget top-level before show().
        overlay.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        overlay.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)

        self._shutting_down = False
        self._mask_revision = 0
        self._mask_ready = False
        self._geometry_dirty = False
        self._last_pointer_norm: tuple[float, float] | None = None

        import tempfile
        from pathlib import Path

        self._temp = tempfile.TemporaryDirectory(prefix="ecommerce-agent-bg-")
        self._temp_dir = Path(self._temp.name)
        self._cards = [
            frame
            for frame in overlay.findChildren(QFrame)
            if frame.objectName() in _GLASS_NAMES
        ]
        self.card_model = GlassCardModel(overlay, self._cards, self)
        self._geometry_watch: set[QObject] = {overlay, self.central}
        for frame in self._cards:
            current: QWidget | None = frame
            while current is not None:
                self._geometry_watch.add(current)
                if current is overlay:
                    break
                current = current.parentWidget()

        # Keep the exact asset preparation from NativeQuickBackground, including
        # the existing one-time blur radius/quality and local Fuji asset.
        self._prepare_assets()
        qml_path = self._temp_dir / "embedded_background.qml"
        qml_path.write_text(_embedded_qml_source(), encoding="utf-8")

        host = QQuickWidget(self.central)
        host.setObjectName("embeddedQuickBackgroundWidget")
        host.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        host.setClearColor(QColor("#17263a"))
        host.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        host.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        host.setGeometry(self.central.rect())
        host.rootContext().setContextProperty("glassCardModel", self.card_model)
        host.setSource(QUrl.fromLocalFile(str(qml_path)))
        root = host.rootObject()
        if root is None:
            details = "\n".join(error.toString() for error in host.errors())
            raise RuntimeError(
                "Embedded QQuickWidget background failed to load"
                + (f":\n{details}" if details else "")
            )

        self.quick_widget: QQuickWidget | None = host
        self.engine = host.engine()
        self.quick_window: _EmbeddedQuickSurface | None = _EmbeddedQuickSurface(
            host,
            root,
            overlay,
            self,
        )
        self.quick_window.setProperty("sharpUrl", QUrl.fromLocalFile(str(self._sharp_path)))
        self.quick_window.setProperty("blurUrl", QUrl.fromLocalFile(str(self._blur_path)))
        host.show()
        host.lower()

        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(_GEOMETRY_SYNC_MS)
        self._geometry_timer.timeout.connect(self._flush_geometry)

        self._pointer_timer = QTimer(self)
        self._pointer_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._pointer_timer.setInterval(_POINTER_SAMPLE_MS)
        self._pointer_timer.timeout.connect(self._sample_pointer)
        self._pointer_timer.start()

        for watched in self._geometry_watch:
            watched.installEventFilter(self)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

        for area in overlay.findChildren(QAbstractScrollArea):
            area.verticalScrollBar().valueChanged.connect(self.schedule_mask_update)
            area.horizontalScrollBar().valueChanged.connect(self.schedule_mask_update)

        overlay.destroyed.connect(self.shutdown)
        self.quick_window.widthChanged.connect(self.schedule_mask_update)
        self.quick_window.heightChanged.connect(self.schedule_mask_update)
        QTimer.singleShot(0, self._sync_host_geometry)
        QTimer.singleShot(0, self.schedule_mask_update)

    def _sync_host_geometry(self) -> None:
        host = self.quick_widget
        if self._shutting_down or host is None:
            return
        try:
            geometry = self.central.rect()
            if host.geometry() != geometry:
                host.setGeometry(geometry)
            host.lower()
        except RuntimeError:
            pass

    def _flush_geometry(self) -> None:
        if self._shutting_down:
            return
        try:
            if (
                not self.overlay.isVisible()
                or self.overlay.windowState() & Qt.WindowState.WindowMinimized
            ):
                self._geometry_dirty = True
                return
        except RuntimeError:
            return

        self._geometry_dirty = False
        changed = self.card_model.sync_geometry()
        if changed or not self._mask_ready:
            self._update_mask_texture()
        if self._geometry_dirty and not self._geometry_timer.isActive():
            self._geometry_timer.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.central and event_type in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        }:
            QTimer.singleShot(0, self._sync_host_geometry)

        if watched in self._geometry_watch and event_type in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.LayoutRequest,
            QEvent.Type.ParentChange,
            QEvent.Type.WindowStateChange,
        }:
            self.schedule_mask_update()
        return False

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._geometry_timer.stop()
        self._pointer_timer.stop()
        for watched in tuple(self._geometry_watch):
            try:
                watched.removeEventFilter(self)
            except RuntimeError:
                pass

        quick = self.quick_window
        self.quick_window = None
        if quick is not None:
            try:
                quick.setProperty("animationRunning", False)
                quick.releaseResources()
            except RuntimeError:
                pass
            quick.deleteLater()

        host = self.quick_widget
        self.quick_widget = None
        if host is not None:
            try:
                host.hide()
                host.setSource(QUrl())
                host.deleteLater()
            except RuntimeError:
                pass

        try:
            self.engine.clearComponentCache()
        except RuntimeError:
            pass
        app = QApplication.instance()
        if app is not None:
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._temp.cleanup()


def activate_embedded_quick_background() -> None:
    """Select the single-composition renderer before NativeVisualStyleController is built."""

    from . import native_visual_style

    native_visual_style.NativeQuickBackground = EmbeddedQuickBackground  # type: ignore[assignment]


__all__ = [
    "EmbeddedQuickBackground",
    "activate_embedded_quick_background",
]
