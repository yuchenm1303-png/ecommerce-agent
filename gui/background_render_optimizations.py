from __future__ import annotations

import hashlib
import os
import struct
import sys
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QStandardPaths, Qt, qVersion
from PySide6.QtGui import QCursor, QImage
from PySide6.QtWidgets import QMainWindow

from . import native_background as _native


_CACHE_MAGIC = b"ECBGRAW1"
_CACHE_HEADER = struct.Struct("<8sIIII")
_CACHE_VERSION = "preblur-raw-v1"


def _preblur_cache_path(source: QImage, radius: float) -> Path | None:
    try:
        root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        if not root:
            return None
        asset_digest = hashlib.sha256(_native._WALLPAPER_ASSET.read_bytes()).hexdigest()
        identity = "|".join(
            (
                _CACHE_VERSION,
                asset_digest,
                qVersion(),
                sys.platform,
                sys.byteorder,
                f"radius={float(radius):.6f}",
                f"size={source.width()}x{source.height()}",
                f"dpr={source.devicePixelRatio():.6f}",
            )
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return Path(root) / "background" / f"fuji-preblur-{digest}.raw"
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _load_raw_preblur(path: Path, source: QImage) -> QImage | None:
    try:
        data = path.read_bytes()
        if len(data) < _CACHE_HEADER.size:
            return None
        magic, width, height, bytes_per_line, payload_size = _CACHE_HEADER.unpack_from(data)
        if magic != _CACHE_MAGIC:
            return None
        if width != source.width() or height != source.height():
            return None
        if width <= 0 or height <= 0 or bytes_per_line <= 0:
            return None
        expected = bytes_per_line * height
        if payload_size != expected or len(data) != _CACHE_HEADER.size + expected:
            return None
        payload = data[_CACHE_HEADER.size :]
        image = QImage(
            payload,
            width,
            height,
            bytes_per_line,
            QImage.Format.Format_ARGB32_Premultiplied,
        ).copy()
        if image.isNull() or image.size() != source.size():
            return None
        return image
    except (OSError, RuntimeError, TypeError, ValueError, struct.error):
        return None


def _store_raw_preblur(path: Path, image: QImage) -> None:
    temp_path: Path | None = None
    try:
        if image.isNull() or image.format() != QImage.Format.Format_ARGB32_Premultiplied:
            return
        width = int(image.width())
        height = int(image.height())
        bytes_per_line = int(image.bytesPerLine())
        payload_size = bytes_per_line * height
        if width <= 0 or height <= 0 or payload_size <= 0:
            return
        payload = bytes(memoryview(image.constBits())[:payload_size])
        if len(payload) != payload_size:
            return
        header = _CACHE_HEADER.pack(
            _CACHE_MAGIC,
            width,
            height,
            bytes_per_line,
            payload_size,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temp_path.write_bytes(header + payload)
        os.replace(temp_path, path)
        temp_path = None
    except (OSError, RuntimeError, TypeError, ValueError, BufferError, struct.error):
        return
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def install_preblur_cache() -> None:
    """Cache the exact pre-JPEG blur pixels without changing the visual pipeline.

    NativeQuickBackground still saves the returned QImage as the same JPG quality
    and QML still renders the same sharp image, pre-blurred image, global mask and
    MultiEffect. Only the expensive one-time QGraphicsBlurEffect calculation is
    skipped on later launches when the exact raw pixels are available.
    """

    original = _native._blur_wallpaper
    if bool(getattr(original, "_ecommerce_preblur_cached", False)):
        return

    @wraps(original)
    def cached_blur(source: QImage, radius: float = 10.0) -> QImage:
        cache_path = _preblur_cache_path(source, radius)
        if cache_path is not None:
            cached = _load_raw_preblur(cache_path, source)
            if cached is not None:
                return cached

        result = original(source, radius)
        if cache_path is not None and not result.isNull():
            _store_raw_preblur(cache_path, result)
        return result

    cached_blur._ecommerce_preblur_cached = True  # type: ignore[attr-defined]
    _native._blur_wallpaper = cached_blur


class BackgroundPointerHotpath(QObject):
    """Cheaper Python -> Quick pointer bridge with identical parallax semantics.

    The established 8 ms timer, normalization, epsilon, target properties and QML
    FrameAnimation are preserved. The hot path removes only redundant work:
    QCursor is read once per active tick, QQuickWindow geometry is cached by its
    own change signals, and the previous runtime wrapper's second cursor lookup is
    avoided.
    """

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.background = getattr(visual, "background", None)
        self.quick = getattr(self.background, "quick_window", None)
        self.timer = getattr(self.background, "_pointer_timer", None)
        runtime = getattr(window, "_ui_runtime_optimizations", None)
        self.fallback: Callable[[], None] | None = getattr(
            runtime,
            "_original_pointer_sample",
            None,
        )
        self._last_global: tuple[int, int] | None = None
        self._last_geometry: tuple[int, int, int, int] | None = None
        self._geometry: tuple[int, int, int, int] | None = None
        self.installed = False

        if self.background is None or self.quick is None or self.timer is None:
            return
        if not callable(self.fallback):
            candidate = getattr(self.background, "_sample_pointer", None)
            self.fallback = candidate if callable(candidate) else None

        current = getattr(self.background, "_sample_pointer", None)
        if not callable(current):
            return
        try:
            self.timer.timeout.disconnect(current)
        except (RuntimeError, TypeError):
            return

        self._geometry = self._read_geometry()
        for signal_name in ("xChanged", "yChanged", "widthChanged", "heightChanged"):
            signal = getattr(self.quick, signal_name, None)
            if signal is not None:
                try:
                    signal.connect(self._geometry_changed)
                except (RuntimeError, TypeError):
                    pass

        self.timer.timeout.connect(self.sample)
        self.installed = True

    def _read_geometry(self) -> tuple[int, int, int, int] | None:
        try:
            return (
                int(self.quick.x()),
                int(self.quick.y()),
                int(self.quick.width()),
                int(self.quick.height()),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def _geometry_changed(self, *_args: object) -> None:
        self._geometry = self._read_geometry()

    def _fallback(self) -> None:
        if callable(self.fallback):
            try:
                self.fallback()
            except RuntimeError:
                pass

    def sample(self) -> None:
        bg = self.background
        quick = self.quick
        if bg is None or quick is None or bool(getattr(bg, "_shutting_down", False)):
            return
        try:
            if not quick.isVisible() or quick.windowState() & Qt.WindowState.WindowMinimized:
                return
            global_pos = QCursor.pos()
            point = (global_pos.x(), global_pos.y())
        except RuntimeError:
            return

        geometry = self._geometry
        if geometry is None:
            geometry = self._read_geometry()
            self._geometry = geometry

        if (
            getattr(bg, "_last_pointer_norm", None) is not None
            and self._last_global == point
            and self._last_geometry == geometry
        ):
            return

        self._last_global = point
        self._last_geometry = geometry
        if geometry is None:
            self._fallback()
            return

        try:
            local = quick.mapFromGlobal(global_pos)
            width = float(geometry[2])
            height = float(geometry[3])
            lx = float(local.x())
            ly = float(local.y())
        except (RuntimeError, TypeError, ValueError):
            self._fallback()
            return

        if (
            width <= 0.0
            or height <= 0.0
            or lx < 0.0
            or ly < 0.0
            or lx > width
            or ly > height
        ):
            nx = 0.0
            ny = 0.0
        else:
            nx = max(-1.0, min(1.0, (lx / max(1.0, width) - 0.5) * 2.0))
            ny = max(-1.0, min(1.0, (ly / max(1.0, height) - 0.5) * 2.0))

        previous = getattr(bg, "_last_pointer_norm", None)
        epsilon = float(getattr(_native, "_POINTER_EPSILON", 0.0015))
        if previous is not None and (
            abs(previous[0] - nx) < epsilon and abs(previous[1] - ny) < epsilon
        ):
            return

        try:
            bg._last_pointer_norm = (nx, ny)  # noqa: SLF001
            quick.setProperty("pointerX", nx)
            quick.setProperty("pointerY", ny)
            quick.setProperty("animationRunning", True)
        except RuntimeError:
            self._fallback()


def install_background_pointer_hotpath(
    window: QMainWindow,
    visual: Any,
) -> BackgroundPointerHotpath | None:
    existing = getattr(window, "_background_pointer_hotpath", None)
    if isinstance(existing, BackgroundPointerHotpath):
        return existing
    controller = BackgroundPointerHotpath(window, visual)
    if not controller.installed:
        controller.deleteLater()
        return None
    window._background_pointer_hotpath = controller  # type: ignore[attr-defined]
    return controller
