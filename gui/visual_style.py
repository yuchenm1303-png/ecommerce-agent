from __future__ import annotations

import base64
import math
import struct
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPainterPath, QPixmap, QRadialGradient
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QMainWindow,
    QScrollBar,
    QWidget,
)


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_WALLPAPER_ASSET = Path(__file__).resolve().parent / "assets" / "fuji_sakura_wallpaper.jpg.b64"
_GL_FLOAT = 0x1406
_GL_TRIANGLE_STRIP = 0x0005
_GL_COLOR_BUFFER_BIT = 0x00004000


NEKRO_STYLE = r"""
QWidget#root {
    color: #ffffff;
    background: transparent;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}

QWidget#workspaceHost,
QWidget#sideHost,
QScrollArea,
QScrollArea > QWidget > QWidget {
    background: transparent;
    border: 0;
}

QFrame#glassCard,
QFrame#heroCard,
QFrame#statusCard,
QFrame#microCard {
    background: transparent;
    border: 0;
    border-radius: 6px;
}

QLabel#brandMark {
    color: rgba(255,255,255,165);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}

QLabel#appTitle {
    color: #ffffff;
    font-size: 31px;
    font-weight: 700;
}

QLabel#subtle,
QLabel#cardHint {
    color: rgba(255,255,255,165);
}

QLabel#cardTitle {
    color: #ffffff;
    font-size: 14px;
    font-weight: 700;
}

QLabel#sectionEyebrow {
    color: rgba(255,255,255,140);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1px;
}

QLabel#phaseBadge {
    padding: 8px 13px;
    color: #efefef;
    background-color: rgba(0,0,0,64);
    border: 0;
    border-radius: 6px;
    font-weight: 600;
}

QLineEdit,
QSpinBox {
    min-height: 38px;
    padding: 0 11px;
    color: #ffffff;
    background-color: rgba(0,0,0,64);
    border: 1px solid rgba(255,255,255,28);
    border-radius: 6px;
    selection-background-color: rgba(255,255,255,64);
    selection-color: #ffffff;
}

QLineEdit:hover,
QSpinBox:hover {
    background-color: rgba(0,0,0,78);
    border-color: rgba(255,255,255,42);
}

QLineEdit:focus,
QSpinBox:focus {
    background-color: rgba(0,0,0,86);
    border-color: rgba(255,255,255,96);
}

QPushButton {
    min-height: 38px;
    padding: 0 16px;
    color: #ffffff;
    background-color: rgba(0,0,0,64);
    border: 0;
    border-radius: 6px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: rgba(0,0,0,102);
}

QPushButton:pressed {
    background-color: rgba(0,0,0,64);
}

QPushButton#primaryButton {
    min-width: 140px;
    color: #ffffff;
    background-color: rgba(255,255,255,48);
    border: 0;
    font-weight: 700;
}

QPushButton#primaryButton:hover {
    background-color: rgba(255,255,255,72);
}

QPushButton#dangerButton {
    background-color: rgba(70,0,18,86);
}

QPushButton#dangerButton:hover {
    background-color: rgba(92,0,24,118);
}

QPushButton#quietButton {
    background-color: rgba(0,0,0,64);
}

QPushButton:disabled {
    color: rgba(255,255,255,76);
    background-color: rgba(0,0,0,34);
}

QCheckBox {
    spacing: 8px;
    color: rgba(255,255,255,210);
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    background-color: rgba(0,0,0,64);
    border: 1px solid rgba(255,255,255,72);
    border-radius: 4px;
}

QCheckBox::indicator:checked {
    background-color: rgba(255,255,255,118);
    border-color: rgba(255,255,255,190);
}

QTableWidget {
    color: #ffffff;
    background-color: rgba(0,0,0,42);
    alternate-background-color: rgba(255,255,255,9);
    border: 0;
    border-radius: 6px;
    gridline-color: rgba(255,255,255,14);
    selection-background-color: rgba(255,255,255,42);
    selection-color: #ffffff;
}

QTableWidget::item {
    padding: 7px 8px;
    border-bottom: 1px solid rgba(255,255,255,12);
}

QHeaderView::section {
    padding: 8px 8px;
    color: rgba(255,255,255,220);
    background-color: rgba(255,255,255,24);
    border: 0;
    border-bottom: 1px solid rgba(255,255,255,20);
    font-weight: 650;
}

QPlainTextEdit {
    color: #efefef;
    background-color: rgba(0,0,0,64);
    border: 0;
    border-radius: 6px;
    padding: 9px;
    selection-background-color: rgba(255,255,255,54);
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
}

QScrollBar:vertical {
    width: 6px;
    background: transparent;
    margin: 0;
}

QScrollBar::handle:vertical {
    min-height: 24px;
    border-radius: 3px;
    background: #eeeeee;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}

QScrollBar:horizontal {
    height: 6px;
    background: transparent;
    margin: 0;
}

QScrollBar::handle:horizontal {
    min-width: 24px;
    border-radius: 3px;
    background: #eeeeee;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
    width: 0;
    background: transparent;
}

QSplitter::handle {
    background: transparent;
    width: 12px;
    height: 12px;
}
"""


_VERTEX_SHADER = """
attribute highp vec2 a_position;
attribute highp vec2 a_uv;
varying highp vec2 v_uv;
void main() {
    gl_Position = vec4(a_position, 0.0, 1.0);
    v_uv = a_uv;
}
"""

_FRAGMENT_SHADER = """
uniform sampler2D u_sharp;
uniform sampler2D u_blur;
uniform sampler2D u_mask;
uniform highp vec4 u_source_rect;
varying highp vec2 v_uv;
void main() {
    highp vec2 wallpaper_uv = u_source_rect.xy + v_uv * u_source_rect.zw;
    lowp vec4 sharp = texture2D(u_sharp, wallpaper_uv);
    lowp vec4 blurred = texture2D(u_blur, wallpaper_uv);
    lowp float glass = texture2D(u_mask, v_uv).a;
    gl_FragColor = mix(sharp, blurred, glass);
}
"""


def _load_wallpaper() -> QPixmap:
    try:
        encoded = _WALLPAPER_ASSET.read_text(encoding="ascii")
        compact = "".join(encoded.split())
        data = base64.b64decode(compact, validate=True)
    except OSError as exc:
        raise RuntimeError(f"Wallpaper asset cannot be read: {_WALLPAPER_ASSET}") from exc
    except ValueError as exc:
        raise RuntimeError(f"Wallpaper asset is not valid base64: {_WALLPAPER_ASSET}") from exc

    if len(data) <= 100_000 or not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        raise RuntimeError(
            "Wallpaper asset is not a complete JPEG: "
            f"path={_WALLPAPER_ASSET}, bytes={len(data)}"
        )

    image = QImage.fromData(data)
    if image.isNull():
        raise RuntimeError(
            "Qt could not decode the bundled wallpaper image: "
            f"path={_WALLPAPER_ASSET}, bytes={len(data)}"
        )

    pixmap = QPixmap.fromImage(image)
    if pixmap.isNull():
        raise RuntimeError(f"Qt could not create a wallpaper pixmap: {_WALLPAPER_ASSET}")
    return pixmap


def _blur_pixmap(source: QPixmap, radius: float = 10.0) -> QPixmap:
    if source.isNull():
        return QPixmap()

    result = QPixmap(source.size())
    result.fill(Qt.transparent)
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(source)
    blur = QGraphicsBlurEffect()
    blur.setBlurRadius(radius)
    item.setGraphicsEffect(blur)
    scene.addItem(item)

    painter = QPainter(result)
    scene.render(
        painter,
        QRectF(result.rect()),
        QRectF(source.rect()),
        Qt.IgnoreAspectRatio,
    )
    painter.end()
    return result


def _compose_vignette(cover: QPixmap) -> QPixmap:
    composed = cover.copy()
    painter = QPainter(composed)
    center = QPointF(composed.width() / 2.0, composed.height() / 2.0)
    radius = max(1.0, math.hypot(composed.width() / 2.0, composed.height() / 2.0))
    outer = QRadialGradient(center, radius)
    outer.setColorAt(0.0, QColor(0, 0, 0, 0))
    outer.setColorAt(1.0, QColor(0, 0, 0, 92))
    painter.fillRect(composed.rect(), outer)
    painter.end()
    return composed


class BackgroundLayer(QOpenGLWidget):
    """One-pass GPU wallpaper + live glass compositor.

    Wallpaper and blur textures are built/uploaded only after startup or resize.
    Runtime parallax changes one fractional source rectangle; every card's glass
    area comes from a mask texture that changes only with UI geometry.
    """

    _OVERSCAN = 1.06
    _TRAVEL = 0.9
    _EASE_PER_16MS = 0.12
    _EASE_TAU = -0.016 / math.log(1.0 - _EASE_PER_16MS)
    _SETTLE_PX = 0.02

    def __init__(self, window: QMainWindow) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)

        self._source = _load_wallpaper()
        self._cover_size = QSize()
        self._pending_sharp: QImage | None = None
        self._pending_blur: QImage | None = None
        self._pending_mask: QImage | None = None
        self._sharp_texture: QOpenGLTexture | None = None
        self._blur_texture: QOpenGLTexture | None = None
        self._mask_texture: QOpenGLTexture | None = None
        self._program: QOpenGLShaderProgram | None = None
        self._vbo: QOpenGLBuffer | None = None
        self._gl_ready = False

        self._offset = QPointF(0.0, 0.0)
        self._target = QPointF(0.0, 0.0)
        self._pending_pointer: QPoint | None = None
        self._motion_active = False
        self._last_frame_time = 0.0

        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(140)
        self._rebuild_timer.timeout.connect(self._rebuild_wallpaper)

        self.window.setMouseTracking(True)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.frameSwapped.connect(self._on_frame_swapped)
        self.window.destroyed.connect(self._detach)

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        size_changed = self.size() != central.size()
        self.setGeometry(central.rect())
        self.lower()
        self.show()
        if self._cover_size.isEmpty():
            self._rebuild_wallpaper()
        elif size_changed:
            self._rebuild_timer.start()

    def set_glass_mask(self, mask: QImage) -> None:
        self._pending_mask = QImage(mask)
        self.update()

    def parallax_source_rect(self) -> QRectF:
        if self._cover_size.isEmpty() or self.width() <= 0 or self.height() <= 0:
            return QRectF()
        width = float(self.width())
        height = float(self.height())
        source_w = width
        source_h = height
        center_x = self._cover_size.width() / 2.0 + self._offset.x()
        center_y = self._cover_size.height() / 2.0 + self._offset.y()
        source_x = max(0.0, min(self._cover_size.width() - source_w, center_x - source_w / 2.0))
        source_y = max(0.0, min(self._cover_size.height() - source_h, center_y - source_h / 2.0))
        return QRectF(source_x, source_y, source_w, source_h)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        del watched
        if event.type() == QEvent.MouseMove and hasattr(event, "globalPosition"):
            self._pending_pointer = event.globalPosition().toPoint()
            self._ensure_motion()
        return False

    def _ensure_motion(self) -> None:
        if self._motion_active:
            return
        self._motion_active = True
        self._last_frame_time = time.perf_counter()
        self.update()

    def _update_target(self, global_pos: QPoint) -> None:
        local = self.mapFromGlobal(global_pos)
        rect = self.rect()
        if not rect.contains(local) or self._cover_size.isEmpty():
            self._target = QPointF(0.0, 0.0)
            return
        half_w = max(1.0, rect.width() / 2.0)
        half_h = max(1.0, rect.height() / 2.0)
        nx = max(-1.0, min(1.0, (local.x() - half_w) / half_w))
        ny = max(-1.0, min(1.0, (local.y() - half_h) / half_h))
        travel_x = max(0.0, (self._cover_size.width() - rect.width()) / 2.0 * self._TRAVEL)
        travel_y = max(0.0, (self._cover_size.height() - rect.height()) / 2.0 * self._TRAVEL)
        self._target = QPointF(-nx * travel_x, -ny * travel_y)

    def _advance_motion(self) -> None:
        if self._pending_pointer is not None:
            point = self._pending_pointer
            self._pending_pointer = None
            self._update_target(point)

        now = time.perf_counter()
        dt = max(0.0, min(0.05, now - self._last_frame_time))
        self._last_frame_time = now
        alpha = 1.0 - math.exp(-dt / self._EASE_TAU) if dt > 0.0 else 0.0
        self._offset = QPointF(
            self._offset.x() + (self._target.x() - self._offset.x()) * alpha,
            self._offset.y() + (self._target.y() - self._offset.y()) * alpha,
        )

        remaining = math.hypot(
            self._target.x() - self._offset.x(),
            self._target.y() - self._offset.y(),
        )
        if remaining <= self._SETTLE_PX:
            self._offset = QPointF(self._target)
            self._motion_active = False

    def _on_frame_swapped(self) -> None:
        if self._motion_active and self.isVisible() and not self.window.isMinimized():
            self.update()

    def _rebuild_wallpaper(self) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        cover_w = max(1, round(self.width() * self._OVERSCAN))
        cover_h = max(1, round(self.height() * self._OVERSCAN))
        scaled = self._source.scaled(
            QSize(cover_w, cover_h),
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        x = max(0, (scaled.width() - cover_w) // 2)
        y = max(0, (scaled.height() - cover_h) // 2)
        cover = scaled.copy(x, y, cover_w, cover_h)
        sharp = _compose_vignette(cover)
        blurred = _blur_pixmap(cover, 10.0)

        self._cover_size = cover.size()
        self._pending_sharp = sharp.toImage()
        self._pending_blur = blurred.toImage()
        self._offset = QPointF(0.0, 0.0)
        self._target = QPointF(0.0, 0.0)
        self.update()

    @staticmethod
    def _make_texture(image: QImage) -> QOpenGLTexture:
        texture = QOpenGLTexture(image, QOpenGLTexture.MipMapGeneration.DontGenerateMipMaps)
        texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
        texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        return texture

    def _replace_texture(self, current: QOpenGLTexture | None, image: QImage) -> QOpenGLTexture:
        if current is not None:
            current.destroy()
        return self._make_texture(image)

    def _upload_pending_textures(self) -> None:
        if self._pending_sharp is not None:
            self._sharp_texture = self._replace_texture(self._sharp_texture, self._pending_sharp)
            self._pending_sharp = None
        if self._pending_blur is not None:
            self._blur_texture = self._replace_texture(self._blur_texture, self._pending_blur)
            self._pending_blur = None
        if self._pending_mask is not None:
            self._mask_texture = self._replace_texture(self._mask_texture, self._pending_mask)
            self._pending_mask = None

    def initializeGL(self) -> None:  # type: ignore[override]
        program = QOpenGLShaderProgram(self)
        if not program.addShaderFromSourceCode(QOpenGLShader.Vertex, _VERTEX_SHADER):
            raise RuntimeError("OpenGL vertex shader compilation failed: " + program.log())
        if not program.addShaderFromSourceCode(QOpenGLShader.Fragment, _FRAGMENT_SHADER):
            raise RuntimeError("OpenGL fragment shader compilation failed: " + program.log())
        if not program.link():
            raise RuntimeError("OpenGL shader link failed: " + program.log())
        self._program = program

        vertices = struct.pack(
            "16f",
            -1.0, -1.0, 0.0, 1.0,
             1.0, -1.0, 1.0, 1.0,
            -1.0,  1.0, 0.0, 0.0,
             1.0,  1.0, 1.0, 0.0,
        )
        vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        if not vbo.create() or not vbo.bind():
            raise RuntimeError("OpenGL vertex buffer creation failed")
        vbo.allocate(vertices, len(vertices))
        vbo.release()
        self._vbo = vbo
        self._gl_ready = True
        self._upload_pending_textures()

    def paintGL(self) -> None:  # type: ignore[override]
        if self._motion_active:
            self._advance_motion()
        self._upload_pending_textures()

        functions = self.context().functions()
        functions.glClearColor(0.09, 0.15, 0.23, 1.0)
        functions.glClear(_GL_COLOR_BUFFER_BIT)

        if (
            not self._gl_ready
            or self._program is None
            or self._vbo is None
            or self._sharp_texture is None
            or self._blur_texture is None
            or self._mask_texture is None
            or self._cover_size.isEmpty()
        ):
            return

        source = self.parallax_source_rect()
        if source.isEmpty():
            return
        source_normalized = (
            source.x() / self._cover_size.width(),
            source.y() / self._cover_size.height(),
            source.width() / self._cover_size.width(),
            source.height() / self._cover_size.height(),
        )

        program = self._program
        if not program.bind() or not self._vbo.bind():
            return

        pos = program.attributeLocation("a_position")
        uv = program.attributeLocation("a_uv")
        program.enableAttributeArray(pos)
        program.enableAttributeArray(uv)
        program.setAttributeBuffer(pos, _GL_FLOAT, 0, 2, 16)
        program.setAttributeBuffer(uv, _GL_FLOAT, 8, 2, 16)

        self._sharp_texture.bind(0)
        self._blur_texture.bind(1)
        self._mask_texture.bind(2)
        program.setUniformValue("u_sharp", 0)
        program.setUniformValue("u_blur", 1)
        program.setUniformValue("u_mask", 2)
        program.setUniformValue("u_source_rect", *source_normalized)

        functions.glDrawArrays(_GL_TRIANGLE_STRIP, 0, 4)

        self._mask_texture.release()
        self._blur_texture.release()
        self._sharp_texture.release()
        program.disableAttributeArray(uv)
        program.disableAttributeArray(pos)
        self._vbo.release()
        program.release()

    def cleanup_gl(self) -> None:
        if not self.isValid():
            return
        self.makeCurrent()
        for texture in (self._sharp_texture, self._blur_texture, self._mask_texture):
            if texture is not None:
                texture.destroy()
        self._sharp_texture = None
        self._blur_texture = None
        self._mask_texture = None
        if self._vbo is not None:
            self._vbo.destroy()
            self._vbo = None
        if self._program is not None:
            self._program.removeAllShaders()
            self._program = None
        self.doneCurrent()
        self._gl_ready = False

    def _detach(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._motion_active = False


class GlassBackdrop(QWidget):
    """Card-local tint only; live blur is already composited by BackgroundLayer."""

    def __init__(self, frame: QFrame, mask_changed: Callable[[], None]) -> None:
        super().__init__(frame)
        self.frame = frame
        self._mask_changed = mask_changed
        self._surface_scale = 1.0
        self._overlay_alpha = 64.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.sync_geometry()

    @property
    def surface_scale(self) -> float:
        return self._surface_scale

    @property
    def overlay_alpha(self) -> float:
        return self._overlay_alpha

    def set_interaction(self, *, scale: float, overlay_alpha: float) -> None:
        scale = max(0.94, min(1.0, float(scale)))
        overlay_alpha = max(0.0, min(255.0, float(overlay_alpha)))
        scale_changed = abs(scale - self._surface_scale) >= 0.0001
        alpha_changed = abs(overlay_alpha - self._overlay_alpha) >= 0.1
        if not scale_changed and not alpha_changed:
            return
        self._surface_scale = scale
        self._overlay_alpha = overlay_alpha
        if scale_changed:
            self._mask_changed()
        if alpha_changed or scale_changed:
            self.update()

    def sync_geometry(self) -> None:
        self.setGeometry(self.frame.rect())
        self.lower()
        self.show()
        self.update()

    def glass_rect(self) -> QRectF:
        rect = QRectF(self.frame.rect())
        if self._surface_scale < 0.9999:
            inset_x = rect.width() * (1.0 - self._surface_scale) * 0.5
            inset_y = rect.height() * (1.0 - self._surface_scale) * 0.5
            rect.adjust(inset_x, inset_y, -inset_x, -inset_y)
        return rect

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.glass_rect()
        path = QPainterPath()
        path.addRoundedRect(rect, 6.0, 6.0)
        painter.fillPath(path, QColor(0, 0, 0, int(round(self._overlay_alpha))))
        painter.end()


class VisualStyleController(QObject):
    """Own one GPU backdrop and lightweight card tint overlays."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.background = BackgroundLayer(window)
        self._glass: dict[QFrame, GlassBackdrop] = {}
        self._cursor_installed = False
        self._mask_rebuild_queued = False

        window.setStyleSheet(window.styleSheet() + "\n" + NEKRO_STYLE)
        for frame in window.findChildren(QFrame):
            if frame.objectName() in _GLASS_NAMES:
                frame.setAttribute(Qt.WA_StyledBackground, True)
                self._glass[frame] = GlassBackdrop(frame, self._schedule_mask_rebuild)

        self._install_cursor()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        for bar in window.findChildren(QScrollBar):
            bar.valueChanged.connect(self._schedule_mask_rebuild)

        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self._sync_all)

    def surface_for(self, frame: QFrame) -> GlassBackdrop | None:
        return self._glass.get(frame)

    def _install_cursor(self) -> None:
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.white)
        painter.drawEllipse(QRectF(1.0, 1.0, 8.0, 8.0))
        painter.end()
        QApplication.setOverrideCursor(QCursor(pixmap, 4, 4))
        self._cursor_installed = True

    def _sync_all(self) -> None:
        self.background.sync_geometry()
        for backdrop in self._glass.values():
            backdrop.sync_geometry()
        self._rebuild_mask()

    def _schedule_mask_rebuild(self, *_args) -> None:
        if self._mask_rebuild_queued:
            return
        self._mask_rebuild_queued = True
        QTimer.singleShot(0, self._rebuild_mask)

    def _rebuild_mask(self) -> None:
        self._mask_rebuild_queued = False
        central = self.window.centralWidget()
        if central is None or central.width() <= 0 or central.height() <= 0:
            return

        dpr = max(1.0, float(self.window.devicePixelRatioF()))
        image = QImage(
            max(1, int(math.ceil(central.width() * dpr))),
            max(1, int(math.ceil(central.height() * dpr))),
            QImage.Format.Format_RGBA8888,
        )
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(dpr, dpr)
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.white)

        for frame, backdrop in self._glass.items():
            if not frame.isVisibleTo(self.window) or frame.width() <= 0 or frame.height() <= 0:
                continue
            top_left = frame.mapTo(central, QPoint(0, 0))
            local = backdrop.glass_rect()
            rect = QRectF(
                top_left.x() + local.x(),
                top_left.y() + local.y(),
                local.width(),
                local.height(),
            )
            path = QPainterPath()
            path.addRoundedRect(rect, 6.0, 6.0)
            painter.drawPath(path)

        painter.end()
        self.background.set_glass_mask(image)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.window:
            if event_type in (QEvent.Resize, QEvent.Show):
                QTimer.singleShot(0, self._sync_all)
            elif event_type == QEvent.Close:
                self.background.cleanup_gl()

        if isinstance(watched, QFrame) and watched in self._glass:
            if event_type in (QEvent.Resize, QEvent.Move, QEvent.Show, QEvent.Hide):
                QTimer.singleShot(0, self._glass[watched].sync_geometry)
                self._schedule_mask_rebuild()
        return False

    def _cleanup(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self._cursor_installed:
            QApplication.restoreOverrideCursor()
            self._cursor_installed = False


def install_visual_style(window: QMainWindow) -> VisualStyleController:
    controller = VisualStyleController(window)
    window._visual_style = controller  # type: ignore[attr-defined]
    return controller
