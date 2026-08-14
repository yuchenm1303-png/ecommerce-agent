from __future__ import annotations

import hashlib
import os
import struct
import sys
from functools import wraps
from pathlib import Path

from PySide6.QtCore import QStandardPaths, qVersion
from PySide6.QtGui import QImage

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
    """Cache the one startup blur computation; no runtime presentation hooks."""

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


__all__ = ["install_preblur_cache"]
