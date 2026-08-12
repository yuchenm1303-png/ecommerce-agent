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
    """Card geometry plus lightweight interaction state consumed by Quick.

    Single-page cards keep stable base coordinates. Continuous page scrolling is
    represented by one QML group offset, so a scrollbar tick never walks the card
    list, repaints a CPU mask, or republishes per-card geometry.
    """

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
    SCALE_ROLE = _ROLE_BASE + 11
    SCROLL_GROUP_ROLE = _ROLE_BASE + 12

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
        SCROLL_GROUP_ROLE,
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
        SCALE_ROLE: "cardScale",
        SCROLL_GROUP_ROLE: "cardScrolls",
    }

    def __init__(self, overlay: QMainWindow, cards: list[QFrame], parent: QObject) -> None:
        super().__init__(parent)
        self.overlay = overlay
        self.cards = cards
        self._rows = {frame: row for row, frame in enumerate(cards)}
        self._scroll_area: QAbstractScrollArea | None = None
        self._scroll_page: QWidget | None = None
        self._states = [self._new_state() for _ in cards]
        self.sync_geometry()

    @staticmethod
    def _new_state() -> dict[str, float | bool]:
        return {
            "cardX": 0.0,
            "cardY": 0.0,
            "cardW": 0.0,
            "cardH": 0.0,
            "clipX": 0.0,
            "clipY": 0.0,
            "clipW": 0.0,
            "clipH": 0.0,
            "cardAlpha": _NORMAL_GLASS_ALPHA,
            "cardScale": 1.0,
            "cardVisible": False,
            "cardScrolls": False,
        }

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
            self.SCALE_ROLE: QByteArray(b"cardScale"),
            self.SCROLL_GROUP_ROLE: QByteArray(b"cardScrolls"),
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._states)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):  # noqa: ANN201
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._states):
            return None
        key = self._ROLE_KEYS.get(role)
        return self._states[index.row()].get(key) if key is not None else None

    def append_card(self, frame: QFrame) -> None:
        row = len(self.cards)
        self.cards.append(frame)
        self._rows[frame] = row
        self._states.append(self._new_state())

    def set_scroll_context(
        self,
        area: QAbstractScrollArea | None,
        page: QWidget | None,
    ) -> None:
        self._scroll_area = area
        self._scroll_page = page
        self.sync_geometry()

    def _is_scroll_card(self, frame: QFrame) -> bool:
        page = self._scroll_page
        return bool(page is not None and (frame is page or page.isAncestorOf(frame)))

    def _scroll_value(self) -> float:
        area = self._scroll_area
        if area is None:
            return 0.0
        try:
            return float(area.verticalScrollBar().value())
        except RuntimeError:
            return 0.0

    @classmethod
    def _empty_snapshot(cls, *, scrolls: bool = False) -> dict[str, float | bool]:
        state = cls._new_state()
        state["cardScrolls"] = scrolls
        return state

    def _snapshot(self, frame: QFrame) -> dict[str, float | bool]:
        scrolls = self._is_scroll_card(frame)
        if (
            not frame.isVisibleTo(self.overlay)
            or frame.width() <= 0
            or frame.height() <= 0
            or self.overlay.width() <= 0
            or self.overlay.height() <= 0
        ):
            return self._empty_snapshot(scrolls=scrolls)

        top_left = frame.mapTo(self.overlay, QPoint(0, 0))
        card_rect = QRectF(
            float(top_left.x()),
            float(top_left.y()),
            float(frame.width()),
            float(frame.height()),
        )
        if scrolls:
            # Normalize today's screen-space position back into a stable page-base
            # coordinate. QML subtracts singleScrollY from the whole group.
            card_rect.translate(0.0, self._scroll_value())

        clip_rect = QRectF(
            0.0,
            0.0,
            float(self.overlay.width()),
            float(self.overlay.height()),
        )
        page = self._scroll_page if scrolls else None

        ancestor = frame.parentWidget()
        while ancestor is not None:
            if not ancestor.isVisibleTo(self.overlay):
                hidden = self._empty_snapshot(scrolls=scrolls)
                hidden.update(
                    {
                        "cardX": card_rect.x(),
                        "cardY": card_rect.y(),
                        "cardW": card_rect.width(),
                        "cardH": card_rect.height(),
                    }
                )
                return hidden

            # Ancestors inside the moving page are layout structure. Their current
            # screen-space rectangles move with the page and therefore must not be
            # baked into a clip that is supposed to survive future scroll values.
            # Fixed ancestors above the page (notably the scroll viewport) remain
            # the real clip boundary for the GPU mask.
            inside_scroll_page = bool(
                page is not None and (ancestor is page or page.isAncestorOf(ancestor))
            )
            if not inside_scroll_page:
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

        visible = not clip_rect.isEmpty()
        if not scrolls:
            visible = visible and not card_rect.intersected(clip_rect).isEmpty()
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
            "cardScrolls": scrolls,
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
                if key in {"cardAlpha", "cardScale"}:
                    continue
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

    def set_presentation(self, frame: QFrame, *, scale: float, alpha: float) -> None:
        row = self._rows.get(frame)
        if row is None:
            return

        scale = max(0.96, min(1.04, float(scale)))
        alpha = max(0.0, min(255.0, float(alpha)))
        state = self._states[row]
        changed_roles: list[int] = []

        if abs(float(state["cardScale"]) - scale) >= 0.0001:
            state["cardScale"] = scale
            changed_roles.append(self.SCALE_ROLE)
        if abs(float(state["cardAlpha"]) - alpha) >= 0.1:
            state["cardAlpha"] = alpha
            changed_roles.append(self.ALPHA_ROLE)

        if not changed_roles:
            return
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, changed_roles)

    def set_alpha(self, frame: QFrame, alpha: float) -> None:
        row = self._rows.get(frame)
        if row is None:
            return
        self.set_presentation(
            frame,
            scale=float(self._states[row].get("cardScale", 1.0)),
            alpha=alpha,
        )

    def render_mask(self, width: int, height: int) -> QImage:
        """Legacy/debug CPU mask renderer; the formal runtime no longer calls it."""

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
        scroll_y = self._scroll_value()

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
                float(state["cardY"]) - (scroll_y if bool(state.get("cardScrolls")) else 0.0),
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
    property real pointerX: 0.0
    property real pointerY: 0.0
    property real offsetX: 0.0
    property real offsetY: 0.0
    property real singleScrollY: 0.0
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

    // GPU-resident mask. Geometry/layout changes update model rows, while smooth
    // scrolling only changes root.singleScrollY, transforming every scrolling
    // mask rectangle together in the scene graph. No QImage/PNG/upload loop.
    Item {{
        id: glassMask
        anchors.fill: parent
        visible: false
        layer.enabled: true
        layer.smooth: true

        Repeater {{
            model: glassCardModel
            delegate: Item {{
                readonly property real visualY: cardY - (cardScrolls ? root.singleScrollY : 0.0)
                x: clipX
                y: clipY
                width: clipW
                height: clipH
                clip: true
                visible: cardVisible && width > 0 && height > 0

                Rectangle {{
                    x: cardX - clipX
                    y: parent.visualY - clipY
                    width: cardW
                    height: cardH
                    radius: {_GLASS_RADIUS:.1f}
                    antialiasing: true
                    color: "white"
                }}
            }}
        }}
    }}

    MultiEffect {{
        anchors.fill: parent
        source: blurSource
        maskEnabled: true
        maskSource: glassMask
        autoPaddingEnabled: false
    }}

    Repeater {{
        model: glassCardModel
        delegate: Item {{
            // Resting cards obey their fixed viewport/ancestor clip. During the
            // existing hover/return transform the shell intentionally switches to
            // window coordinates (overflow visible), preserving the proven hover
            // behaviour instead of clipping at the scroll viewport.
            readonly property real visualY: cardY - (cardScrolls ? root.singleScrollY : 0.0)
            readonly property bool overflowVisible: Math.abs(cardScale - 1.0) > 0.0001
            x: overflowVisible ? 0 : clipX
            y: overflowVisible ? 0 : clipY
            width: overflowVisible ? root.width : clipW
            height: overflowVisible ? root.height : clipH
            clip: !overflowVisible
            visible: cardVisible && (overflowVisible || (width > 0 && height > 0))

            Rectangle {{
                x: cardX - (parent.overflowVisible ? 0 : clipX)
                y: parent.visualY - (parent.overflowVisible ? 0 : clipY)
                width: cardW
                height: cardH
                scale: cardScale
                transformOrigin: Item.Center
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
    """Native Quick wallpaper, GPU glass and parallax renderer under widgets."""

    def __init__(self, overlay: QMainWindow) -> None:
        super().__init__(overlay)
        self.overlay = overlay
        self._shutting_down = False
        self._mask_revision = 0
        self._mask_ready = False
        self._geometry_dirty = False
        self._gpu_mask_enabled = True
        self._single_scroll_area: QAbstractScrollArea | None = None
        self._single_scroll_page: QWidget | None = None
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
        self.quick_window.setProperty("singleScrollY", 0.0)
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

        # Nested/static scroll areas may genuinely change individual card layout or
        # clipping. The final outer Single page is created later and gets a special
        # O(1) transform binding via bind_single_page_scroll().
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

    def bind_single_page_scroll(self, area: QAbstractScrollArea, page: QWidget) -> None:
        """Bind the outer Single scroll to one Quick scene transform."""

        if self._shutting_down:
            return
        if self._single_scroll_area is area and self._single_scroll_page is page:
            self._publish_single_scroll(area.verticalScrollBar().value())
            return

        self._single_scroll_area = area
        self._single_scroll_page = page
        self.card_model.set_scroll_context(area, page)
        area.verticalScrollBar().valueChanged.connect(self._publish_single_scroll)
        self._publish_single_scroll(area.verticalScrollBar().value())
        self.schedule_mask_update()

    def _publish_single_scroll(self, value: int) -> None:
        """The complete continuous-scroll hot path: publish one scalar to Quick."""

        quick = self.quick_window
        if self._shutting_down or quick is None:
            return
        try:
            quick.setProperty("singleScrollY", float(value))
            # Hover neighbour geometry uses this cheap revision as its cache key.
            # Incrementing it is O(1) and intentionally does not schedule geometry
            # scans or rebuild any mask surface.
            self._mask_revision += 1
        except RuntimeError:
            return

    def set_card_alpha(self, frame: QFrame, alpha: float) -> None:
        self.card_model.set_alpha(frame, alpha)

    def set_card_presentation(self, frame: QFrame, *, scale: float, alpha: float) -> None:
        """Update only the GPU shell/tint; mask geometry stays independent."""

        self.card_model.set_presentation(frame, scale=scale, alpha=alpha)

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
            nx = max(-1.0, min(1.0, (local.x() / max(1.0, float(quick.width())) - 0.5) * 2.0))
            ny = max(-1.0, min(1.0, (local.y() / max(1.0, float(quick.height())) - 0.5) * 2.0))

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
        """Coalesce actual layout/visibility changes, never continuous page scroll."""

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
            # QML Repeater roles are already live GPU mask geometry. Revision is
            # retained only as a cheap cache invalidator for QWidget hover logic.
            self._mask_revision += 1
            self._mask_ready = True

        if self._geometry_dirty and not self._geometry_timer.isActive():
            self._geometry_timer.start()

    def _update_mask_texture(self) -> None:
        """Compatibility hook: formal runtime uses QML geometry, not mask textures."""

        self._mask_revision += 1
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
