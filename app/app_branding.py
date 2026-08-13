from __future__ import annotations

import base64
import hashlib
import sys

from app.app_icon_data import APP_ICON_JPEG_BASE64, APP_ICON_SHA256


def application_icon_bytes() -> bytes:
    raw = base64.b64decode(APP_ICON_JPEG_BASE64, validate=True)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != APP_ICON_SHA256:
        raise RuntimeError("Embedded application icon failed integrity validation")
    return raw


def apply_qt_application_icon(app) -> None:  # noqa: ANN001
    """Apply the approved app artwork to every top-level Qt window/taskbar entry."""

    from PySide6.QtGui import QIcon, QPixmap

    pixmap = QPixmap()
    if not pixmap.loadFromData(application_icon_bytes(), "JPG"):
        raise RuntimeError("Qt could not decode the embedded application icon")
    app.setWindowIcon(QIcon(pixmap))

    # Give Windows one stable application identity so the taskbar and shortcuts
    # consistently use the packaged EcommerceAgent icon rather than a Qt/Python
    # fallback icon.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
                "EcommerceAgent.ListingStudio"
            )
        except Exception:
            pass


__all__ = ["application_icon_bytes", "apply_qt_application_icon"]
