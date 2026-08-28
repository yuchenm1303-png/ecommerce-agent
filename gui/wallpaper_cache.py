from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSize, QStandardPaths, Qt, qVersion
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap, QRegion
from PySide6.QtWidgets import QFrame, QMainWindow, QWidget

from .native_background import (
    NativeQuickBackground,
    _GLASS_RADIUS,
    _NORMAL_GLASS_ALPHA,
    _OVERSCAN,
    _WALLPAPER_ASSET,
    _blur_wallpaper,
    _decode_wallpaper,
)


_CACHE_VERSION = "materialized-jpeg-v1"
_JPEG_QUALITY = 92
_prepared_assets: tuple[Path, Path] | None = None


def _cache_root() -> Path:
    for location in (
        QStandardPaths.StandardLocation.CacheLocation,
        QStandardPaths.StandardLocation.AppLocalDataLocation,
    ):
        value = QStandardPaths.writableLocation(location)
        if value:
            root = Path(value) / "background"
            root.mkdir(parents=True, exist_ok=True)
            return root
    raise RuntimeError("Qt did not provide a writable wallpaper cache location")


def _cache_identity() -> str:
    try:
        asset_digest = hashlib.sha256(_WALLPAPER_ASSET.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError(f"Wallpaper asset cannot be read: {_WALLPAPER_ASSET}") from exc

    identity = "|".join(
        (
            _CACHE_VERSION,
            asset_digest,
            qVersion(),
            sys.platform,
            sys.byteorder,
            f"quality={_JPEG_QUALITY}",
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _jpeg_file_is_ready(path: Path) -> bool:
    try:
        if path.stat().st_size <= 4096:
            return False
        with path.open("rb") as stream:
            if stream.read(3) != b"\xff\xd8\xff":
                return False
            stream.seek(-2, os.SEEK_END)
            return stream.read(2) == b"\xff\xd9"
    except OSError:
        return False


def _atomic_write(path: Path, data: bytes) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_save_jpeg(path: Path, image: QImage) -> None:
    temp = path.with_name(f".{path.stem}.{os.getpid()}.tmp.jpg")
    try:
        if not image.save(str(temp), "JPG", _JPEG_QUALITY):
            raise RuntimeError("Failed to encode the persistent blurred wallpaper")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def prepare_wallpaper_assets() -> tuple[Path, Path]:
    """Materialize the exact Quick wallpaper pair once and reuse it thereafter.

    The sharp file is the bundled JPEG byte-for-byte. The blurred file is produced
    by the same Qt ``QGraphicsBlurEffect`` path and JPEG quality used by the native
    background before this cache existed. A content/Qt/platform identity prevents a
    stale visual asset from surviving a source image or renderer change.
    """

    global _prepared_assets
    prepared = _prepared_assets
    if prepared is not None and all(_jpeg_file_is_ready(path) for path in prepared):
        return prepared

    identity = _cache_identity()
    root = _cache_root()
    sharp_path = root / f"fuji-sharp-{identity}.jpg"
    blur_path = root / f"fuji-blurred-{identity}.jpg"

    if not _jpeg_file_is_ready(sharp_path):
        _atomic_write(sharp_path, _decode_wallpaper())

    if not _jpeg_file_is_ready(blur_path):
        source = QImage(str(sharp_path))
        if source.isNull():
            source_data = _decode_wallpaper()
            _atomic_write(sharp_path, source_data)
            source = QImage.fromData(source_data)
        if source.isNull():
            raise RuntimeError("Qt could not decode the bundled wallpaper image")

        blurred = _blur_wallpaper(source)
        if blurred.isNull():
            raise RuntimeError("Failed to create the pre-blurred wallpaper")
        _atomic_save_jpeg(blur_path, blurred)

    if not _jpeg_file_is_ready(sharp_path) or not _jpeg_file_is_ready(blur_path):
        raise RuntimeError("Persistent wallpaper assets were not materialized correctly")

    _prepared_assets = (sharp_path, blur_path)
    return _prepared_assets


def install_preblur_cache() -> tuple[Path, Path]:
    """Warm the persistent wallpaper pair before the native Quick scene is built."""

    return prepare_wallpaper_assets()


def _centered_static_view(source: QPixmap, width: int, height: int) -> QPixmap:
    """Render the centered, zero-drift view with the same 1.06x cover geometry as Quick."""

    width = max(1, int(width))
    height = max(1, int(height))
    outer_width = max(width, int(round(width * _OVERSCAN)))
    outer_height = max(height, int(round(height * _OVERSCAN)))
    scaled = source.scaled(
        QSize(outer_width, outer_height),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    crop_x = max(0, (scaled.width() - outer_width) // 2)
    crop_y = max(0, (scaled.height() - outer_height) // 2)
    overscanned = scaled.copy(crop_x, crop_y, outer_width, outer_height)
    view_x = max(0, (outer_width - width) // 2)
    view_y = max(0, (outer_height - height) // 2)
    return overscanned.copy(view_x, view_y, width, height)


class _StaticWallpaperScene(QWidget):
    """Direct, damage-driven QWidget renderer for the zero-drift scene.

    Wallpaper views are resized only when the native root size changes. Geometry
    changes merely refresh the lightweight card rectangles and invalidate the old
    and new glass regions; no full-window intermediate pixmap is ever composed.
    """

    _DAMAGE_PAD = 2

    def __init__(
        self,
        overlay: QMainWindow,
        *,
        card_model,
        sharp_path: Path,
        blur_path: Path,
    ) -> None:
        central = overlay.centralWidget()
        if central is None:
            raise RuntimeError("Static wallpaper renderer requires a central widget")
        super().__init__(central)
        self.overlay = overlay
        self.card_model = card_model
        self._central = central
        self._sharp_source = QPixmap(str(sharp_path))
        self._blur_source = QPixmap(str(blur_path))
        if self._sharp_source.isNull() or self._blur_source.isNull():
            raise RuntimeError("Static wallpaper renderer could not load cached assets")
        self._cached_root_size: tuple[int, int] | None = None
        self._sharp_view = QPixmap()
        self._blur_view = QPixmap()
        self._origin = QPoint(0, 0)
        self._card_records: dict[QFrame, tuple[QRectF, QRectF]] = {}
        self.setObjectName("staticWallpaperScene")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self.setGeometry(central.rect())
        self.lower()

    def _record_region(self, record: tuple[QRectF, QRectF] | None) -> QRegion:
        if record is None:
            return QRegion()
        visible = record[0].intersected(record[1])
        if visible.isEmpty():
            return QRegion()
        rect = visible.toAlignedRect().adjusted(
            -self._DAMAGE_PAD,
            -self._DAMAGE_PAD,
            self._DAMAGE_PAD,
            self._DAMAGE_PAD,
        )
        rect = rect.intersected(self.rect())
        return QRegion(rect) if not rect.isEmpty() else QRegion()

    def _snapshot_cards(self, origin: QPoint) -> dict[QFrame, tuple[QRectF, QRectF]]:
        records: dict[QFrame, tuple[QRectF, QRectF]] = {}
        for frame in tuple(self.card_model.cards):
            try:
                snapshot = self.card_model._snapshot(frame)
            except RuntimeError:
                continue
            if not bool(snapshot["cardVisible"]):
                continue

            card_rect = QRectF(
                float(snapshot["cardX"]) - origin.x(),
                float(snapshot["cardY"]) - origin.y(),
                float(snapshot["cardW"]),
                float(snapshot["cardH"]),
            )
            clip_rect = QRectF(
                float(snapshot["clipX"]) - origin.x(),
                float(snapshot["clipY"]) - origin.y(),
                float(snapshot["clipW"]),
                float(snapshot["clipH"]),
            )
            if card_rect.isEmpty() or clip_rect.isEmpty():
                continue
            if card_rect.intersected(clip_rect).isEmpty():
                continue
            records[frame] = (card_rect, clip_rect)
        return records

    def rebuild(self) -> None:
        if self.overlay.width() <= 0 or self.overlay.height() <= 0:
            return
        if self._central.width() <= 0 or self._central.height() <= 0:
            return

        previous_geometry = self.geometry()
        target_geometry = self._central.rect()
        geometry_changed = previous_geometry != target_geometry
        if geometry_changed:
            self.setGeometry(target_geometry)

        root_width = int(self.overlay.width())
        root_height = int(self.overlay.height())
        root_size = (root_width, root_height)
        root_size_changed = self._cached_root_size != root_size
        if root_size_changed:
            self._sharp_view = _centered_static_view(
                self._sharp_source,
                root_width,
                root_height,
            )
            self._blur_view = _centered_static_view(
                self._blur_source,
                root_width,
                root_height,
            )
            self._cached_root_size = root_size

        origin = self._central.mapTo(self.overlay, QPoint(0, 0))
        origin_changed = origin != self._origin
        previous_records = self._card_records
        current_records = self._snapshot_cards(origin)
        self._origin = origin
        self._card_records = current_records
        self.lower()

        if root_size_changed or geometry_changed or origin_changed:
            self.update()
            return

        damage = QRegion()
        for frame in set(previous_records) | set(current_records):
            old_record = previous_records.get(frame)
            new_record = current_records.get(frame)
            if old_record == new_record:
                continue
            damage = damage.united(self._record_region(old_record))
            damage = damage.united(self._record_region(new_record))

        if not damage.isEmpty():
            self.update(damage)

    def paintEvent(self, event) -> None:  # noqa: N802, ANN001
        if self._sharp_view.isNull() or self._blur_view.isNull():
            return

        damage = event.region()
        painter = QPainter(self)
        painter.setClipRegion(damage)
        painter.drawPixmap(-self._origin.x(), -self._origin.y(), self._sharp_view)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        for card_rect, clip_rect in self._card_records.values():
            visible_rect = card_rect.intersected(clip_rect)
            if visible_rect.isEmpty() or not damage.intersects(visible_rect.toAlignedRect()):
                continue

            path = QPainterPath()
            path.addRoundedRect(card_rect, _GLASS_RADIUS, _GLASS_RADIUS)
            painter.save()
            painter.setClipRect(clip_rect, Qt.ClipOperation.IntersectClip)
            painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
            painter.drawPixmap(-self._origin.x(), -self._origin.y(), self._blur_view)
            painter.restore()

        painter.end()


class _StaticCardTint(QWidget):
    """Card tint that scales with the existing resident QWidget card effect."""

    def __init__(self, frame: QFrame) -> None:
        super().__init__(frame)
        self._alpha = _NORMAL_GLASS_ALPHA
        self.setObjectName("staticGlassTint")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setGeometry(frame.rect())
        self.lower()

    def set_alpha(self, alpha: float) -> None:
        alpha = max(0.0, min(255.0, float(alpha)))
        if abs(self._alpha - alpha) < 0.1:
            return
        self._alpha = alpha
        self.update()

    def sync_geometry(self) -> None:
        frame = self.parentWidget()
        if frame is None:
            return
        self.setGeometry(frame.rect())
        self.lower()

    def paintEvent(self, _event) -> None:  # noqa: N802, ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, int(round(self._alpha))))
        painter.drawRoundedRect(QRectF(self.rect()), _GLASS_RADIUS, _GLASS_RADIUS)
        painter.end()


class PersistentNativeQuickBackground(NativeQuickBackground):
    """Dual-mode renderer with a minimal steady-state static path.

    The QQuickWindow remains the native HWND owner. Static mode hides its entire
    Quick content tree, disables persistent scene-graph/graphics retention and
    releases GPU resources; QWidget alone paints the fixed wallpaper/glass scene.
    Dynamic mode restores the original Quick content and keeps the static QWidget
    cover until the first presented Quick frame so mode changes never flash blank.
    """

    def __init__(self, overlay: QMainWindow) -> None:
        self._dynamic_mode = False
        self._awaiting_dynamic_frame = False
        self._static_scene: _StaticWallpaperScene | None = None
        self._static_tints: dict[QFrame, _StaticCardTint] = {}
        self._card_presentations: dict[QFrame, tuple[float, float]] = {}
        super().__init__(overlay)

        quick = self.quick_window
        if quick is not None:
            try:
                quick.frameSwapped.connect(self._on_dynamic_frame_ready)
            except (AttributeError, RuntimeError, TypeError):
                pass

        self._static_scene = _StaticWallpaperScene(
            overlay,
            card_model=self.card_model,
            sharp_path=self._sharp_path,
            blur_path=self._blur_path,
        )
        self._sync_static_cards()
        self.pause_pointer_animation()
        self._static_scene.rebuild()
        self._static_scene.show()
        self._static_scene.lower()
        self._set_quick_scene_active(False, release=True)

    @property
    def dynamic_mode(self) -> bool:
        return self._dynamic_mode

    def _prepare_assets(self) -> None:
        self._sharp_path, self._blur_path = prepare_wallpaper_assets()

    def _static_cover_visible(self) -> bool:
        return bool(not self._dynamic_mode or self._awaiting_dynamic_frame)

    def _set_quick_scene_active(self, active: bool, *, release: bool = False) -> None:
        quick = self.quick_window
        if quick is None:
            return
        try:
            content = quick.contentItem()
            if active:
                quick.setPersistentGraphics(True)
                quick.setPersistentSceneGraph(True)
                if content is not None:
                    content.setVisible(True)
                quick.requestUpdate()
                return

            quick.setProperty("animationRunning", False)
            if content is not None:
                content.setVisible(False)
            quick.setPersistentGraphics(False)
            quick.setPersistentSceneGraph(False)
            if release:
                quick.releaseResources()
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _ensure_static_tint(self, frame: QFrame) -> _StaticCardTint:
        tint = self._static_tints.get(frame)
        if tint is None:
            tint = _StaticCardTint(frame)
            self._static_tints[frame] = tint
        tint.sync_geometry()
        return tint

    def _sync_static_cards(self) -> None:
        active_frames = set(self.card_model.cards)
        static_visible = self._static_cover_visible()
        for frame in tuple(active_frames):
            try:
                tint = self._ensure_static_tint(frame)
            except RuntimeError:
                continue
            scale, alpha = self._card_presentations.get(
                frame,
                (1.0, _NORMAL_GLASS_ALPHA),
            )
            self._card_presentations[frame] = (scale, alpha)
            tint.set_alpha(alpha)
            tint.setVisible(static_visible)
            if static_visible:
                tint.lower()

        for frame, tint in tuple(self._static_tints.items()):
            if frame in active_frames:
                continue
            try:
                tint.hide()
                tint.deleteLater()
            except RuntimeError:
                pass
            self._static_tints.pop(frame, None)
            self._card_presentations.pop(frame, None)

    def _on_dynamic_frame_ready(self) -> None:
        if self._shutting_down or not self._dynamic_mode or not self._awaiting_dynamic_frame:
            return
        self._awaiting_dynamic_frame = False
        scene = self._static_scene
        if scene is not None:
            try:
                scene.hide()
            except RuntimeError:
                pass
        self._sync_static_cards()

    def set_dynamic_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._shutting_down:
            return
        if enabled == self._dynamic_mode:
            if not enabled:
                self.schedule_mask_update()
            return

        quick = self.quick_window
        if enabled:
            # Restore exactly the Quick/GPU presentation state before exposing it.
            self.card_model.sync_geometry()
            for frame, (scale, alpha) in tuple(self._card_presentations.items()):
                self.card_model.set_presentation(frame, scale=scale, alpha=alpha)
            self._geometry_revision += 1
            if quick is not None:
                try:
                    quick.setProperty("geometryRevision", self._geometry_revision)
                    quick.setProperty("animationRunning", False)
                except RuntimeError:
                    pass

            self._dynamic_mode = True
            self._awaiting_dynamic_frame = True
            self._sync_static_cards()
            scene = self._static_scene
            if scene is not None:
                try:
                    scene.show()
                    scene.lower()
                except RuntimeError:
                    pass
            self._set_quick_scene_active(True)
            self.reset_pointer_identity()
            return

        # Make the complete QWidget scene visible before suspending/releasing Quick.
        self._dynamic_mode = False
        self._awaiting_dynamic_frame = False
        self._sync_static_cards()
        scene = self._static_scene
        if scene is not None:
            scene.rebuild()
            scene.show()
            scene.lower()
        if quick is not None:
            try:
                quick.setProperty("animationRunning", False)
                quick.setProperty("pointerX", 0.0)
                quick.setProperty("pointerY", 0.0)
                quick.setProperty("offsetX", 0.0)
                quick.setProperty("offsetY", 0.0)
            except RuntimeError:
                pass
        self._set_quick_scene_active(False, release=True)
        self.reset_pointer_identity()

    def set_card_alpha(self, frame: QFrame, alpha: float) -> None:
        scale, _ = self._card_presentations.get(frame, (1.0, _NORMAL_GLASS_ALPHA))
        self.set_card_presentation(frame, scale=scale, alpha=alpha)

    def set_card_presentation(self, frame: QFrame, *, scale: float, alpha: float) -> None:
        scale = max(0.96, min(1.04, float(scale)))
        alpha = max(0.0, min(255.0, float(alpha)))
        self._card_presentations[frame] = (scale, alpha)
        if self._dynamic_mode:
            super().set_card_presentation(frame, scale=scale, alpha=alpha)
            return

        try:
            tint = self._ensure_static_tint(frame)
            tint.set_alpha(alpha)
            tint.show()
            tint.lower()
        except RuntimeError:
            pass

    def presentation_tick(self, global_pos: QPoint, *, input_changed: bool) -> None:
        if not self._dynamic_mode:
            return
        super().presentation_tick(global_pos, input_changed=input_changed)

    def _flush_geometry(self) -> None:
        if self._shutting_down:
            return
        if self._dynamic_mode:
            super()._flush_geometry()
            if self._awaiting_dynamic_frame and self._static_scene is not None:
                self._static_scene.rebuild()
            return

        self._geometry_dirty = False
        self._sync_static_cards()
        if self._static_scene is not None:
            self._static_scene.rebuild()
            self._static_scene.show()
            self._static_scene.lower()

        if self._geometry_dirty and not self._geometry_timer.isActive():
            self._geometry_timer.start()

    def shutdown(self) -> None:
        quick = self.quick_window
        if quick is not None:
            try:
                quick.frameSwapped.disconnect(self._on_dynamic_frame_ready)
            except (AttributeError, RuntimeError, TypeError):
                pass

        self._awaiting_dynamic_frame = False
        scene = self._static_scene
        self._static_scene = None
        if scene is not None:
            try:
                scene.hide()
                scene.deleteLater()
            except RuntimeError:
                pass
        for tint in tuple(self._static_tints.values()):
            try:
                tint.hide()
                tint.deleteLater()
            except RuntimeError:
                pass
        self._static_tints.clear()
        self._card_presentations.clear()
        super().shutdown()


__all__ = [
    "PersistentNativeQuickBackground",
    "install_preblur_cache",
    "prepare_wallpaper_assets",
]
