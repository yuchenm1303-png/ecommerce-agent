from __future__ import annotations

import base64
import math
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QFrame, QMainWindow, QWidget


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_WALLPAPER_ASSET = Path(__file__).resolve().parent / "assets" / "fuji_sakura_wallpaper.jpg.b64"
_OVERSCAN = 1.06
_TRAVEL = 0.90
_GLASS_RADIUS = 6.0


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

    from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene
    from PySide6.QtGui import QPixmap

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
        id: blurHost
        width: root.width
        height: root.height
        clip: true
        Image {{
            id: blurImg
            width: root.width * {_OVERSCAN}
            height: root.height * {_OVERSCAN}
            x: root.imageX
            y: root.imageY
            source: root.blurUrl
            fillMode: Image.PreserveAspectCrop
            smooth: false
            cache: true
        }}
    }}

    Image {{
        id: maskImg
        source: root.maskUrl
        width: root.width
        height: root.height
        visible: false
        cache: false
        asynchronous: false
    }}

    ShaderEffect {{
        anchors.fill: parent
        property variant blurTex: blurHost
        property variant maskTex: maskImg
        fragmentShader: "
            uniform sampler2D blurTex;
            uniform sampler2D maskTex;
            varying vec2 qt_TexCoord0;
            void main() {{
                vec4 blur = texture2D(blurTex, qt_TexCoord0);
                vec4 mask = texture2D(maskTex, qt_TexCoord0);
                gl_FragColor = vec4(blur.rgb, blur.a * mask.a);
            }}
        "
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
    """Native QQuickWindow scene-graph background underneath the QWidget GUI."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._shutting_down = False
        self._mask_pending = False
        self._mask_revision = 0
        self._temp = tempfile.TemporaryDirectory(prefix="ecommerce-agent-bg-")
        self._temp_dir = Path(self._temp.name)
        self._cards = [
            frame
            for frame in window.findChildren(QFrame)
            if frame.objectName() in _GLASS_NAMES
        ]
        self._geometry_watch: set[QObject] = {window}
        for frame in self._cards:
            current: QWidget | None = frame
            while current is not None:
                self._geometry_watch.add(current)
                if current is window:
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
        flags = (
            self.quick_window.flags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.quick_window.setFlags(flags)
        self.quick_window.setProperty("sharpUrl", QUrl.fromLocalFile(str(self._sharp_path)))
        self.quick_window.setProperty("blurUrl", QUrl.fromLocalFile(str(self._blur_path)))
        self.quick_window.setPersistentGraphics(False)
        self.quick_window.setPersistentSceneGraph(False)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            app.aboutToQuit.connect(self.shutdown)

        for area in window.findChildren(QAbstractScrollArea):
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
        if quick is None:
            return
        if point is None or self.window.width() <= 0 or self.window.height() <= 0:
            nx = 0.0
            ny = 0.0
        else:
            local = self.window.mapFromGlobal(point.toPoint())
            if not self.window.rect().contains(local):
                nx = 0.0
                ny = 0.0
            else:
                nx = max(-1.0, min(1.0, (local.x() / max(1.0, float(self.window.width())) - 0.5) * 2.0))
                ny = max(-1.0, min(1.0, (local.y() / max(1.0, float(self.window.height())) - 0.5) * 2.0))
        quick.setProperty("pointerX", nx)
        quick.setProperty("pointerY", ny)
        quick.setProperty("animationRunning", True)

    def _sync_window(self) -> None:
        quick = self.quick_window
        if self._shutting_down or quick is None:
            return
        frame = self.window.frameGeometry()
        quick.setPosition(frame.topLeft())
        quick.resize(frame.size())
        if self.window.isVisible() and not self.window.isMinimized():
            if not quick.isVisible():
                quick.show()
            self.window.raise_()
            quick.setProperty("animationRunning", True)
        else:
            quick.setProperty("animationRunning", False)
            quick.hide()

    def _card_path(self, frame: QFrame, origin: QPoint) -> QPainterPath | None:
        if not frame.isVisibleTo(self.window) or frame.width() <= 0 or frame.height() <= 0:
            return None

        top_left = frame.mapToGlobal(QPoint(0, 0)) - origin
        rect = QRectF(float(top_left.x()), float(top_left.y()), float(frame.width()), float(frame.height()))
        path = QPainterPath()
        path.addRoundedRect(rect, _GLASS_RADIUS, _GLASS_RADIUS)

        ancestor = frame.parentWidget()
        while ancestor is not None:
            if not ancestor.isVisibleTo(self.window):
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
            if path.isEmpty() or ancestor is self.window:
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
        width = max(1, int(quick.width()))
        height = max(1, int(quick.height()))
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255))
        origin = quick.position()
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

    def _resize_edges(self, global_point: QPointF) -> Qt.Edge:
        local = self.window.mapFromGlobal(global_point.toPoint())
        margin = 6
        edges = Qt.Edge(0)
        if local.x() <= margin:
            edges |= Qt.Edge.LeftEdge
        elif local.x() >= self.window.width() - margin:
            edges |= Qt.Edge.RightEdge
        if local.y() <= margin:
            edges |= Qt.Edge.TopEdge
        elif local.y() >= self.window.height() - margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _is_drag_surface(self, watched: QObject) -> bool:
        if not isinstance(watched, QWidget):
            return False
        return watched.objectName() in {"brandMark", "appTitle", "subtle"}

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.window:
            if event_type in {QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.WindowStateChange}:
                self._sync_window()
                self.schedule_mask_update()
            elif event_type == QEvent.Type.Hide:
                quick = self.quick_window
                if quick is not None:
                    quick.setProperty("animationRunning", False)
                    quick.hide()
            elif event_type == QEvent.Type.Close:
                self.shutdown()
            elif event_type == QEvent.Type.Leave:
                self._set_animation_target(None)

        if watched in self._geometry_watch and event_type in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.LayoutRequest,
            QEvent.Type.ParentChange,
        }:
            self.schedule_mask_update()

        if isinstance(event, QMouseEvent):
            if event_type == QEvent.Type.MouseMove:
                self._set_animation_target(event.globalPosition())
            elif event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                edges = self._resize_edges(event.globalPosition())
                handle = self.window.windowHandle()
                if edges and handle is not None and handle.startSystemResize(edges):
                    return True
                if self._is_drag_surface(watched) and handle is not None and handle.startSystemMove():
                    return True
            elif event_type == QEvent.Type.MouseButtonDblClick and self._is_drag_surface(watched):
                if self.window.isMaximized():
                    self.window.showNormal()
                else:
                    self.window.showMaximized()
                return True

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
            quick.hide()
            quick.releaseResources()
            quick.close()
            quick.deleteLater()
        self.engine.clearComponentCache()
        self.engine.deleteLater()
        if app is not None:
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._temp.cleanup()
