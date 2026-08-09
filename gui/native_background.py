from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRectF,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QCursor, QImage, QPainter
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QFrame, QMainWindow, QWidget


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_WALLPAPER_ASSET = Path(__file__).resolve().parent / "assets" / "fuji_sakura_wallpaper.jpg.b64"
_OVERSCAN = 1.06
_TRAVEL = 0.90
_GLASS_RADIUS = 6.0
_NORMAL_GLASS_ALPHA = 64.0
_GEOMETRY_SYNC_MS = 24
_POINTER_SAMPLE_MS = 8
_POINTER_EPSILON = 0.0015


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


class GlassCardModel(QAbstractListModel):
    """Card geometry plus lightweight tint state consumed by the Quick scene."""

    _ROLE_BASE = int(Qt.ItemDataRole.UserRole)
    CARD_X_ROLE = _ROLE_BASE + 1
    CARD_Y_ROLE = _ROLE_BASE + 2
    CARD_W_ROLE = _ROLE_BASE + 3
    CARD_H_ROLE = _ROLE_BASE + 4
    CLIP_X_ROLE = _ROLE_BASE + 5
    CLIP_Y_ROLE = _ROLE_BASE + 6
    CLIP_W_ROLE = _ROLE_BASE + 7
    CLIP_H_ROLE = _ROLE_BASE + 8
    ALPHA_ROLE = _ROLE_BASE + 9
    VISIBLE_ROLE = _ROLE_BASE + 10

    _GEOMETRY_ROLES = [
        CARD_X_ROLE,
        CARD_Y_ROLE,
        CARD_W_ROLE,
        CARD_H_ROLE,
        CLIP_X_ROLE,
        CLIP_Y_ROLE,
        CLIP_W_ROLE,
        CLIP_H_ROLE,
        VISIBLE_ROLE,
    ]
    _ROLE_KEYS = {
        CARD_X_ROLE: "cardX",
        CARD_Y_ROLE: "cardY",
        CARD_W_ROLE: "cardW",
        CARD_H_ROLE: "cardH",
        CLIP_X_ROLE: "clipX",
        CLIP_Y_ROLE: "clipY",
        CLIP_W_ROLE: "clipW",
        CLIP_H_ROLE: "clipH",
        ALPHA_ROLE: "cardAlpha",
        VISIBLE_ROLE: "cardVisible",
    }

    def __init__(self, overlay: QMainWindow, cards: list[QFrame], parent: QObject) -> None:
        super().__init__(parent)
        self.overlay = overlay
        self.cards = cards
        self._rows = {frame: row for row, frame in enumerate(cards)}
        self._states = [
            {
                "cardX": 0.0,
                "cardY": 0.0,
                "cardW": 0.0,
                "cardH": 0.0,
                "clipX": 0.0,
                "clipY": 0.0,
                "clipW": 0.0,
                "clipH": 0.0,
                "cardAlpha": _NORMAL_GLASS_ALPHA,
                "cardVisible": False,
            }
            for _ in cards
        ]
        self.sync_geometry()

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return {
            self.CARD_X_ROLE: QByteArray(b"cardX"),
            self.CARD_Y_ROLE: QByteArray(b"cardY"),
            self.CARD_W_ROLE: QByteArray(b"cardW"),
            self.CARD_H_ROLE: QByteArray(b"cardH"),
            self.CLIP_X_ROLE: QByteArray(b"clipX"),
            self.CLIP_Y_ROLE: QByteArray(b"clipY"),
            self.CLIP_W_ROLE: QByteArray(b"clipW"),
            self.CLIP_H_ROLE: QByteArray(b"clipH"),
            self.ALPHA_ROLE: QByteArray(b"cardAlpha"),
            self.VISIBLE_ROLE: QByteArray(b"cardVisible"),
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._states)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):  # noqa: ANN201
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._states):
            return None
        key = self._ROLE_KEYS.get(role)
        return self._states[index.row()].get(key) if key is not None else None

    def _snapshot(self, frame: QFrame) -> dict[str, float | bool]:
        if (
            not frame.isVisibleTo(self.overlay)
            or frame.width() <= 0
            or frame.height() <= 0
            or self.overlay.width() <= 0
            or self.overlay.height() <= 0
        ):
            return {
                "cardX": 0.0,
                "cardY": 0.0,
                "cardW": 0.0,
                "cardH": 0.0,
                "clipX": 0.0,
                "clipY": 0.0,
                "clipW": 0.0,
                "clipH": 0.0,
                "cardVisible": False,
            }

        top_left = frame.mapTo(self.overlay, QPoint(0, 0))
        card_rect = QRectF(
            float(top_left.x()),
            float(top_left.y()),
            float(frame.width()),
            float(frame.height()),
        )
        clip_rect = QRectF(
            0.0,
            0.0,
            float(self.overlay.width()),
            float(self.overlay.height()),
        )

        ancestor = frame.parentWidget()
        while ancestor is not None:
            if not ancestor.isVisibleTo(self.overlay):
                return {
                    "cardX": card_rect.x(),
                    "cardY": card_rect.y(),
                    "cardW": card_rect.width(),
                    "cardH": card_rect.height(),
                    "clipX": 0.0,
                    "clipY": 0.0,
                    "clipW": 0.0,
                    "clipH": 0.0,
                    "cardVisible": False,
                }
            ancestor_top_left = ancestor.mapTo(self.overlay, QPoint(0, 0))
            ancestor_rect = QRectF(
                float(ancestor_top_left.x()),
                float(ancestor_top_left.y()),
                float(ancestor.width()),
                float(ancestor.height()),
            )
            clip_rect = clip_rect.intersected(ancestor_rect)
            if clip_rect.isEmpty() or ancestor is self.overlay:
                break
            ancestor = ancestor.parentWidget()

        visible_rect = card_rect.intersected(clip_rect)
        visible = not visible_rect.isEmpty()
        return {
            "cardX": card_rect.x(),
            "cardY": card_rect.y(),
            "cardW": card_rect.width(),
            "cardH": card_rect.height(),
            "clipX": clip_rect.x() if visible else 0.0,
            "clipY": clip_rect.y() if visible else 0.0,
            "clipW": clip_rect.width() if visible else 0.0,
            "clipH": clip_rect.height() if visible else 0.0,
            "cardVisible": visible,
        }

    @staticmethod
    def _different(old: object, new: object) -> bool:
        if isinstance(old, float) or isinstance(new, float):
            try:
                return abs(float(old) - float(new)) > 0.01
            except (TypeError, ValueError):
                return old != new
        return old != new

    def sync_geometry(self) -> bool:
        changed_rows: list[int] = []
        for row, frame in enumerate(self.cards):
            snapshot = self._snapshot(frame)
            state = self._states[row]
            changed = False
            for key, value in snapshot.items():
                if self._different(state.get(key), value):
                    state[key] = value
                    changed = True
            if changed:
                changed_rows.append(row)

        if changed_rows:
            self.dataChanged.emit(
                self.index(min(changed_rows), 0),
                self.index(max(changed_rows), 0),
                self._GEOMETRY_ROLES,
            )
            return True
        return False

    def set_alpha(self, frame: QFrame, alpha: float) -> None:
        row = self._rows.get(frame)
        if row is None:
            return
        alpha = max(0.0, min(255.0, float(alpha)))
        state = self._states[row]
        if abs(float(state["cardAlpha"]) - alpha) < 0.1:
            return
        state["cardAlpha"] = alpha
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [self.ALPHA_ROLE])

    def render_mask(self, width: int, height: int) -> QImage:
        image = QImage(
            max(1, int(width)),
            max(1, int(height)),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.GlobalColor.white)

        for state in self._states:
            if not bool(state["cardVisible"]):
                continue
            clip = QRectF(
                float(state["clipX"]),
                float(state["clipY"]),
                float(state["clipW"]),
                float(state["clipH"]),
            )
            card = QRectF(
                float(state["cardX"]),
                float(state["cardY"]),
                float(state["cardW"]),
                float(state["cardH"]),
            )
            if clip.isEmpty() or card.isEmpty():
                continue
            painter.save()
            painter.setClipRect(clip)
            painter.drawRoundedRect(card, _GLASS_RADIUS, _GLASS_RADIUS)
            painter.restore()

        painter.end()
        return image


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
        smooth: true
    }}

    MultiEffect {{
        anchors.fill: parent
        source: blurSource
        maskEnabled: true
        maskSource: maskImg
        autoPaddingEnabled: false
    }}

    Repeater {{
        model: glassCardModel
        delegate: Item {{
            x: clipX
            y: clipY
            width: clipW
            height: clipH
            clip: true
            visible: cardVisible

            Rectangle {{
                x: cardX - clipX
                y: cardY - clipY
                width: cardW
                height: cardH
                radius: {_GLASS_RADIUS:.1f}
                antialiasing: true
                color: Qt.rgba(0, 0, 0, cardAlpha / 255.0)
            }}
        }}
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
    """Native Quick wallpaper, glass and parallax renderer under baseline widgets."""

    def __init__(self, overlay: QMainWindow) -> None:
        super().__init__(overlay)
        self.overlay = overlay
        self._shutting_down = False
        self._mask_revision = 0
        self._mask_ready = False
        self._geometry_dirty = False
        self._last_pointer_norm: tuple[float, float] | None = None
        self._temp = tempfile.TemporaryDirectory(prefix="ecommerce-agent-bg-")
        self._temp_dir = Path(self._temp.name)
        self._cards = [
            frame
            for frame in overlay.findChildren(QFrame)
            if frame.objectName() in _GLASS_NAMES
        ]
        self.card_model = GlassCardModel(overlay, self._cards, self)
        self._geometry_watch: set[QObject] = {overlay}
        for frame in self._cards:
            current: QWidget | None = frame
            while current is not None:
                self._geometry_watch.add(current)
                if current is overlay:
                    break
                current = current.parentWidget()

        self._prepare_assets()
        qml_path = self._temp_dir / "native_background.qml"
        qml_path.write_text(_qml_source(), encoding="utf-8")

        self.engine = QQmlApplicationEngine(self)
        self.engine.rootContext().setContextProperty("glassCardModel", self.card_model)
        self.engine.load(QUrl.fromLocalFile(str(qml_path)))
        roots = self.engine.rootObjects()
        if not roots or not isinstance(roots[0], QQuickWindow):
            raise RuntimeError("Native QQuickWindow background failed to load")

        self.quick_window: QQuickWindow | None = roots[0]
        self.quick_window.setFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.quick_window.setTitle(overlay.windowTitle())
        self.quick_window.resize(overlay.size())
        self.quick_window.setMinimumSize(overlay.minimumSize())
        self.quick_window.setProperty("sharpUrl", QUrl.fromLocalFile(str(self._sharp_path)))
        self.quick_window.setProperty("blurUrl", QUrl.fromLocalFile(str(self._blur_path)))
        self.quick_window.setPersistentGraphics(False)
        self.quick_window.setPersistentSceneGraph(False)

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

    def set_card_alpha(self, frame: QFrame, alpha: float) -> None:
        self.card_model.set_alpha(frame, alpha)

    def _sample_pointer(self) -> None:
        quick = self.quick_window
        if self._shutting_down or quick is None or not quick.isVisible():
            return
        if quick.windowState() & Qt.WindowState.WindowMinimized:
            return

        local = quick.mapFromGlobal(QCursor.pos())
        if quick.width() <= 0 or quick.height() <= 0 or not QRectF(
            0.0,
            0.0,
            float(quick.width()),
            float(quick.height()),
        ).contains(local):
            nx = 0.0
            ny = 0.0
        else:
            nx = max(
                -1.0,
                min(1.0, (local.x() / max(1.0, float(quick.width())) - 0.5) * 2.0),
            )
            ny = max(
                -1.0,
                min(1.0, (local.y() / max(1.0, float(quick.height())) - 0.5) * 2.0),
            )

        previous = self._last_pointer_norm
        if previous is not None and (
            abs(previous[0] - nx) < _POINTER_EPSILON
            and abs(previous[1] - ny) < _POINTER_EPSILON
        ):
            return

        self._last_pointer_norm = (nx, ny)
        quick.setProperty("pointerX", nx)
        quick.setProperty("pointerY", ny)
        quick.setProperty("animationRunning", True)

    def schedule_mask_update(self, *_args: object) -> None:
        """Coalesce card/layout churn; the blur mask is not a per-frame surface."""

        if self._shutting_down:
            return
        self._geometry_dirty = True
        if not self._geometry_timer.isActive():
            self._geometry_timer.start()

    def _flush_geometry(self) -> None:
        if self._shutting_down:
            return
        self._geometry_dirty = False
        changed = self.card_model.sync_geometry()
        if changed or not self._mask_ready:
            self._update_mask_texture()

        # If another layout request landed while this pass was running, leave one
        # trailing update queued so the final geometry always wins.
        if self._geometry_dirty and not self._geometry_timer.isActive():
            self._geometry_timer.start()

    def _update_mask_texture(self) -> None:
        quick = self.quick_window
        if self._shutting_down or quick is None:
            return
        image = self.card_model.render_mask(quick.width(), quick.height())
        self._mask_revision += 1
        mask_path = self._temp_dir / f"glass_mask_{self._mask_revision & 1}.png"
        if not image.save(str(mask_path), "PNG"):
            raise RuntimeError("Failed to update the global glass mask")
        quick.setProperty("maskUrl", QUrl.fromLocalFile(str(mask_path)))
        self._mask_ready = True

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched in self._geometry_watch and event.type() in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.LayoutRequest,
            QEvent.Type.ParentChange,
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
            quick.setProperty("animationRunning", False)
            quick.hide()
            quick.releaseResources()
            quick.close()
            quick.deleteLater()
        self.engine.clearComponentCache()
        self.engine.deleteLater()
        app = QApplication.instance()
        if app is not None:
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._temp.cleanup()
