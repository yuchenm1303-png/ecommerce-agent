from __future__ import annotations

import base64
import ctypes
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QFrame, QMainWindow, QWidget


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_WALLPAPER_ASSET = Path(__file__).resolve().parent / "assets" / "fuji_sakura_wallpaper.jpg.b64"
_OVERSCAN = 1.06
_TRAVEL = 0.90
_GLASS_RADIUS = 6.0
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010


def _place_child_behind(background_wid: int, content_wid: int) -> None:
    """Keep the Quick container directly behind the native QWidget content child."""

    if sys.platform != "win32" or not background_wid or not content_wid:
        return
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = ctypes.c_int
    user32.SetWindowPos(
        ctypes.c_void_p(background_wid),
        ctypes.c_void_p(content_wid),
        0,
        0,
        0,
        0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
    )


def _assert_same_native_parent(background_wid: int, content_wid: int) -> None:
    if sys.platform != "win32" or not background_wid or not content_wid:
        return
    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetParent.restype = ctypes.c_void_p
    background_parent = int(user32.GetParent(ctypes.c_void_p(background_wid)) or 0)
    content_parent = int(user32.GetParent(ctypes.c_void_p(content_wid)) or 0)
    if not background_parent or background_parent != content_parent:
        raise RuntimeError(
            "Quick container and QWidget content are not children of the same native host"
        )


def _decode_wallpaper() -> bytes:
    try:
        encoded = _WALLPAPER_ASSET.read_text(encoding="ascii")
        data = base64.b64decode("".join(encoded.split()), validate=True)
    except OSError as exc:
        raise RuntimeError(f"Wallpaper asset cannot be read: {_WALLPAPER_ASSET}") from exc
    except ValueError as exc:
        raise RuntimeError(f"Wallpaper asset is not valid base64: {_WALLPAPER_ASSET}") from exc

    if len(data) <= 100_000 or not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        raise RuntimeError(
            "Wallpaper asset is not a complete JPEG: "
            f"path={_WALLPAPER_ASSET}, bytes={len(data)}"
        )
    return data


def _blur_wallpaper(source: QImage, radius: float = 10.0) -> QImage:
    """Create the one pre-blurred wallpaper used for the entire window lifetime."""

    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene

    pixmap = QPixmap.fromImage(source)
    item = QGraphicsPixmapItem(pixmap)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    item.setGraphicsEffect(effect)
    scene = QGraphicsScene()
    scene.addItem(item)
    result = QImage(source.size(), QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    scene.render(painter, QRectF(result.rect()), QRectF(pixmap.rect()))
    painter.end()
    scene.removeItem(item)
    item.setGraphicsEffect(None)
    return result


def _qml_source() -> str:
    max_x = (_OVERSCAN - 1.0) * 0.5 * _TRAVEL
    max_y = (_OVERSCAN - 1.0) * 0.5 * _TRAVEL
    return f'''import QtQuick
import QtQuick.Window
import QtQuick.Effects

Window {{
    id: root
    visible: false
    color: "#17263a"

    property url sharpUrl
    property url blurUrl
    property url maskUrl
    property real pointerX: 0.0
    property real pointerY: 0.0
    property real offsetX: 0.0
    property real offsetY: 0.0
    property bool animationRunning: false

    readonly property real maxX: width * {max_x:.9f}
    readonly property real maxY: height * {max_y:.9f}
    readonly property real targetX: -pointerX * maxX
    readonly property real targetY: -pointerY * maxY
    readonly property real imageX: (width - width * {_OVERSCAN}) * 0.5 + offsetX
    readonly property real imageY: (height - height * {_OVERSCAN}) * 0.5 + offsetY

    Image {{
        id: sharpImg
        width: root.width * {_OVERSCAN}
        height: root.height * {_OVERSCAN}
        x: root.imageX
        y: root.imageY
        source: root.sharpUrl
        fillMode: Image.PreserveAspectCrop
        smooth: true
        cache: true
    }}

    Item {{
        id: blurSource
        anchors.fill: parent
        clip: true
        visible: false
        layer.enabled: true
        layer.smooth: true

        Image {{
            id: blurImg
            width: root.width * {_OVERSCAN}
            height: root.height * {_OVERSCAN}
            x: root.imageX
            y: root.imageY
            source: root.blurUrl
            fillMode: Image.PreserveAspectCrop
            smooth: true
            cache: true
        }}
    }}

    Image {{
        id: maskImg
        anchors.fill: parent
        source: root.maskUrl
        visible: false
        cache: false
        asynchronous: false
    }}

    MultiEffect {{
        anchors.fill: parent
        source: blurSource
        maskEnabled: true
        maskSource: maskImg
        autoPaddingEnabled: false
    }}

    FrameAnimation {{
        id: frameDriver
        running: root.animationRunning
        onTriggered: {{
            var dt = Math.max(0.0, Math.min(frameTime, 0.05))
            var gain = 1.0 - Math.pow(0.88, dt * 60.0)
            root.offsetX += (root.targetX - root.offsetX) * gain
            root.offsetY += (root.targetY - root.offsetY) * gain
            if (Math.abs(root.targetX - root.offsetX) < 0.02 &&
                    Math.abs(root.targetY - root.offsetY) < 0.02) {{
                root.offsetX = root.targetX
                root.offsetY = root.targetY
                root.animationRunning = false
            }}
        }}
    }}
}}
'''


class NativeQuickBackground(QObject):
    """QQuick scene graph embedded as one native child below QWidget content."""

    def __init__(
        self,
        window: QMainWindow,
        *,
        surface_host: QWidget,
        content_layer: QWidget,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.surface_host = surface_host
        self.content_layer = content_layer
        self._shutting_down = False
        self._mask_pending = False
        self._mask_revision = 0
        self._temp = tempfile.TemporaryDirectory(prefix="ecommerce-agent-bg-")
        self._temp_dir = Path(self._temp.name)
        self._cards = [
            frame
            for frame in content_layer.findChildren(QFrame)
            if frame.objectName() in _GLASS_NAMES
        ]
        self._geometry_watch: set[QObject] = {window, surface_host, content_layer}
        for frame in self._cards:
            current: QWidget | None = frame
            while current is not None:
                self._geometry_watch.add(current)
                if current is content_layer:
                    break
                current = current.parentWidget()

        self._prepare_assets()
        qml_path = self._temp_dir / "native_background.qml"
        qml_path.write_text(_qml_source(), encoding="utf-8")

        self.engine = QQmlApplicationEngine(self)
        self.engine.load(QUrl.fromLocalFile(str(qml_path)))
        roots = self.engine.rootObjects()
        if not roots or not isinstance(roots[0], QQuickWindow):
            raise RuntimeError("Native QQuickWindow background failed to load")

        self.quick_window: QQuickWindow | None = roots[0]
        # Configure the QWindow before embedding it. QWidget.createWindowContainer
        # is Qt's supported QWidget/QWindow bridge and owns native reparenting,
        # geometry and visibility for the embedded Quick surface.
        self.quick_window.setFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.quick_window.setProperty("sharpUrl", QUrl.fromLocalFile(str(self._sharp_path)))
        self.quick_window.setProperty("blurUrl", QUrl.fromLocalFile(str(self._blur_path)))
        self.quick_window.setPersistentGraphics(False)
        self.quick_window.setPersistentSceneGraph(False)

        self.quick_container = QWidget.createWindowContainer(
            self.quick_window,
            surface_host,
            Qt.WindowType.FramelessWindowHint,
        )
        self.quick_container.setObjectName("nativeQuickBackgroundContainer")
        self.quick_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.quick_container.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.quick_container.setAutoFillBackground(False)
        self.quick_container.setGeometry(surface_host.rect())
        self.quick_container.show()
        self.quick_container.lower()
        content_layer.raise_()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            app.aboutToQuit.connect(self.shutdown)

        for area in content_layer.findChildren(QAbstractScrollArea):
            area.verticalScrollBar().valueChanged.connect(self.schedule_mask_update)
            area.horizontalScrollBar().valueChanged.connect(self.schedule_mask_update)

        window.destroyed.connect(self.shutdown)
        QTimer.singleShot(0, self._sync_window)
        QTimer.singleShot(0, self.schedule_mask_update)

    def _prepare_assets(self) -> None:
        data = _decode_wallpaper()
        self._sharp_path = self._temp_dir / "wallpaper.jpg"
        self._blur_path = self._temp_dir / "wallpaper_blurred.jpg"
        self._sharp_path.write_bytes(data)
        image = QImage.fromData(data)
        if image.isNull():
            raise RuntimeError("Qt could not decode the bundled wallpaper image")
        blurred = _blur_wallpaper(image)
        if blurred.isNull() or not blurred.save(str(self._blur_path), "JPG", 92):
            raise RuntimeError("Failed to create the pre-blurred wallpaper")

    def _set_animation_target(self, point: QPointF | None) -> None:
        quick = self.quick_window
        host = self.surface_host
        if quick is None:
            return
        if point is None or host.width() <= 0 or host.height() <= 0:
            nx = 0.0
            ny = 0.0
        else:
            local = host.mapFromGlobal(point.toPoint())
            if not host.rect().contains(local):
                nx = 0.0
                ny = 0.0
            else:
                nx = max(-1.0, min(1.0, (local.x() / max(1.0, float(host.width())) - 0.5) * 2.0))
                ny = max(-1.0, min(1.0, (local.y() / max(1.0, float(host.height())) - 0.5) * 2.0))
        quick.setProperty("pointerX", nx)
        quick.setProperty("pointerY", ny)
        quick.setProperty("animationRunning", True)

    def _restack_children(self) -> None:
        if self._shutting_down or not self.quick_container.isVisible():
            return
        background_wid = int(self.quick_container.winId())
        content_wid = int(self.content_layer.winId())
        _assert_same_native_parent(background_wid, content_wid)
        _place_child_behind(background_wid, content_wid)
        self.quick_container.stackUnder(self.content_layer)
        self.content_layer.raise_()

    def _sync_window(self) -> None:
        quick = self.quick_window
        if self._shutting_down or quick is None:
            return

        host = self.surface_host
        self.quick_container.setGeometry(host.rect())

        if self.window.isVisible() and host.isVisible() and not self.window.isMinimized():
            if not self.quick_container.isVisible():
                self.quick_container.show()
            quick.setProperty("animationRunning", True)
            QTimer.singleShot(0, self._restack_children)
        else:
            quick.setProperty("animationRunning", False)
            self.quick_container.hide()

    def _card_path(self, frame: QFrame, origin: QPoint) -> QPainterPath | None:
        if not frame.isVisibleTo(self.content_layer) or frame.width() <= 0 or frame.height() <= 0:
            return None

        top_left = frame.mapToGlobal(QPoint(0, 0)) - origin
        rect = QRectF(float(top_left.x()), float(top_left.y()), float(frame.width()), float(frame.height()))
        path = QPainterPath()
        path.addRoundedRect(rect, _GLASS_RADIUS, _GLASS_RADIUS)

        ancestor = frame.parentWidget()
        while ancestor is not None:
            if not ancestor.isVisibleTo(self.content_layer):
                return None
            ancestor_top_left = ancestor.mapToGlobal(QPoint(0, 0)) - origin
            clip_rect = QRectF(
                float(ancestor_top_left.x()),
                float(ancestor_top_left.y()),
                float(ancestor.width()),
                float(ancestor.height()),
            )
            clip_path = QPainterPath()
            clip_path.addRect(clip_rect)
            path = path.intersected(clip_path)
            if path.isEmpty() or ancestor is self.content_layer:
                break
            ancestor = ancestor.parentWidget()
        return None if path.isEmpty() else path

    def schedule_mask_update(self, *_args: object) -> None:
        if self._shutting_down or self._mask_pending:
            return
        self._mask_pending = True
        QTimer.singleShot(0, self._update_mask)

    def _update_mask(self) -> None:
        self._mask_pending = False
        quick = self.quick_window
        if self._shutting_down or quick is None:
            return

        width = max(1, int(self.surface_host.width()))
        height = max(1, int(self.surface_host.height()))
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255))
        origin = self.surface_host.mapToGlobal(QPoint(0, 0))
        for frame in self._cards:
            path = self._card_path(frame, origin)
            if path is not None:
                painter.drawPath(path)
        painter.end()

        self._mask_revision += 1
        mask_path = self._temp_dir / f"glass_mask_{self._mask_revision & 1}.png"
        if not image.save(str(mask_path), "PNG"):
            raise RuntimeError("Failed to update the global glass mask")
        quick.setProperty("maskUrl", QUrl.fromLocalFile(str(mask_path)))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.window:
            if event_type in {QEvent.Type.Show, QEvent.Type.WindowStateChange}:
                self._sync_window()
                self.schedule_mask_update()
            elif event_type == QEvent.Type.Hide:
                quick = self.quick_window
                if quick is not None:
                    quick.setProperty("animationRunning", False)
                self.quick_container.hide()
            elif event_type == QEvent.Type.Close:
                self.shutdown()

        if watched is self.surface_host and event_type in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        }:
            self._sync_window()
            self.schedule_mask_update()

        if watched in self._geometry_watch and event_type in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.LayoutRequest,
            QEvent.Type.ParentChange,
        }:
            self.schedule_mask_update()

        if isinstance(event, QMouseEvent) and event_type == QEvent.Type.MouseMove:
            self._set_animation_target(event.globalPosition())
        elif watched is self.content_layer and event_type == QEvent.Type.Leave:
            self._set_animation_target(None)

        return False

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

        quick = self.quick_window
        self.quick_window = None
        if quick is not None:
            quick.setProperty("animationRunning", False)
            quick.releaseResources()

        self.quick_container.hide()
        self.quick_container.deleteLater()
        self.engine.clearComponentCache()
        self.engine.deleteLater()
        if app is not None:
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._temp.cleanup()
