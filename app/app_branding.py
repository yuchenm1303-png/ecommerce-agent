from __future__ import annotations

import base64
import hashlib
import sys

from app.app_icon_data import APP_ICON_PNG_BASE64, APP_ICON_SHA256


APP_DISPLAY_NAME = "Listing Studio"
APP_ORGANIZATION = "Smirel"
APP_USER_MODEL_ID = "Smirel.ListingStudio"
ICON_VISUAL_SCALE = 1.12


def application_icon_bytes() -> bytes:
    raw = base64.b64decode(APP_ICON_PNG_BASE64, validate=True)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != APP_ICON_SHA256:
        raise RuntimeError("Embedded application icon failed integrity validation")
    return raw


# Raster sizes the packaged ICO carries; the runtime QIcon mirrors them so
# Windows picks a well-matched glyph per context (taskbar 32, titlebar 16,
# Alt-Tab 32, DPI-scaled large taskbars) instead of downscaling one 64px source.
RUNTIME_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def apply_qt_application_icon(app) -> None:  # noqa: ANN001
    """Apply one canonical Windows identity and icon to the whole Qt process."""

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon, QPixmap

    pixmap = QPixmap()
    if not pixmap.loadFromData(application_icon_bytes(), "PNG"):
        raise RuntimeError("Qt could not decode the embedded application icon")

    scaled = pixmap.scaled(
        round(pixmap.width() * ICON_VISUAL_SCALE),
        round(pixmap.height() * ICON_VISUAL_SCALE),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - pixmap.width()) // 2)
    y = max(0, (scaled.height() - pixmap.height()) // 2)
    artwork = scaled.copy(x, y, pixmap.width(), pixmap.height())

    icon = QIcon()
    for size in RUNTIME_ICON_SIZES:
        icon.addPixmap(
            artwork.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    app.setApplicationName(APP_DISPLAY_NAME)
    app.setOrganizationName(APP_ORGANIZATION)
    app.setWindowIcon(icon)

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
                APP_USER_MODEL_ID
            )
        except Exception:
            pass


__all__ = [
    "APP_DISPLAY_NAME",
    "APP_ORGANIZATION",
    "APP_USER_MODEL_ID",
    "application_icon_bytes",
    "apply_qt_application_icon",
]
