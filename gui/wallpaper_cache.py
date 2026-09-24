from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, qVersion
from PySide6.QtGui import QImage

from .native_background import (
    NativeQuickBackground,
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


class PersistentNativeQuickBackground(NativeQuickBackground):
    """Native Quick background backed by persistent, already-prepared JPEG assets."""

    def _prepare_assets(self) -> None:
        self._sharp_path, self._blur_path = prepare_wallpaper_assets()


__all__ = [
    "PersistentNativeQuickBackground",
    "install_preblur_cache",
    "prepare_wallpaper_assets",
]
