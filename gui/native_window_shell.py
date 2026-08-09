from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QMainWindow


_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_WS_EX_LAYERED = 0x00080000
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_TOOLWINDOW = 0x00000080
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020


def _window_long_functions():
    user32 = ctypes.windll.user32
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
    get_long.restype = ctypes.c_ssize_t
    set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    set_long.restype = ctypes.c_ssize_t
    return user32, get_long, set_long


def _embed_native_child(overlay_hwnd: int, owner_hwnd: int) -> None:
    """Make the translucent QWidget surface a true child HWND of Quick.

    The Quick window is the only desktop top-level window.  Keeping the business
    QWidget as a layered child makes Windows own move/resize/Z-order as one
    window tree and removes all screen-coordinate/DPI synchronization.
    """

    if sys.platform != "win32" or not overlay_hwnd or not owner_hwnd:
        return

    user32, get_long, set_long = _window_long_functions()
    overlay = ctypes.c_void_p(overlay_hwnd)
    owner = ctypes.c_void_p(owner_hwnd)

    style = int(get_long(overlay, _GWL_STYLE))
    style = (style | _WS_CHILD) & ~_WS_POPUP
    set_long(overlay, _GWL_STYLE, style)

    exstyle = int(get_long(overlay, _GWL_EXSTYLE))
    exstyle = (exstyle | _WS_EX_LAYERED) & ~(_WS_EX_APPWINDOW | _WS_EX_TOOLWINDOW)
    set_long(overlay, _GWL_EXSTYLE, exstyle)

    user32.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.SetParent.restype = ctypes.c_void_p
    user32.SetParent(overlay, owner)

    user32.SetWindowPos(
        overlay,
        None,
        0,
        0,
        0,
        0,
        _SWP_NOMOVE
        | _SWP_NOSIZE
        | _SWP_NOZORDER
        | _SWP_NOACTIVATE
        | _SWP_FRAMECHANGED,
    )

    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetParent.restype = ctypes.c_void_p
    actual_parent = int(user32.GetParent(overlay) or 0)
    if actual_parent != owner_hwnd:
        raise RuntimeError(
            "QWidget overlay was not embedded under the native Quick application window"
        )


class NativeWindowShell(QObject):
    """One framed QQuickWindow with the existing QWidget UI as a child surface."""

    def __init__(self, overlay: QMainWindow, owner: QQuickWindow) -> None:
        super().__init__(overlay)
        self.overlay = overlay
        self.owner = owner
        self._closing = False
        self._embedded = False

        owner.setTitle("ecommerce-agent · Acceptance Control Console")
        owner.setFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        owner.resize(overlay.size())
        owner.setMinimumSize(overlay.minimumSize())

        # The system frame belongs exclusively to Quick. The old QMainWindow is
        # only a per-pixel-alpha client surface and never exists as a second
        # desktop-level application window after show().
        overlay.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        overlay.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        owner.installEventFilter(self)
        overlay.installEventFilter(self)
        owner.widthChanged.connect(self._sync_overlay_geometry)
        owner.heightChanged.connect(self._sync_overlay_geometry)

    def show(self) -> None:
        self.owner.create()
        self.overlay.winId()
        overlay_handle = self.overlay.windowHandle()
        if overlay_handle is None:
            raise RuntimeError("QWidget overlay has no native window handle")

        # Tell Qt about the native hierarchy first, then enforce the equivalent
        # Win32 child/layered styles. This avoids the previous owned-top-level
        # pair and therefore removes external-window interleaving by design.
        overlay_handle.setParent(self.owner)
        _embed_native_child(int(self.overlay.winId()), int(self.owner.winId()))
        self._embedded = True

        self.owner.show()
        self._sync_overlay_geometry()
        self.overlay.show()
        QTimer.singleShot(0, self._sync_overlay_geometry)

    def _sync_overlay_geometry(self, *_args: object) -> None:
        if self._closing or not self._embedded:
            return

        width = max(1, int(self.owner.width()))
        height = max(1, int(self.owner.height()))
        target = QRect(0, 0, width, height)

        if self.overlay.geometry() != target:
            self.overlay.setGeometry(target)

        handle = self.overlay.windowHandle()
        if handle is not None:
            # QWindow child coordinates are client-local. Never convert through
            # Win32 physical pixels or screen coordinates; Qt owns DPR scaling.
            if handle.position() != QPoint(0, 0):
                handle.setPosition(QPoint(0, 0))
            if handle.width() != width or handle.height() != height:
                handle.resize(width, height)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.owner:
            if event_type in {
                QEvent.Type.Show,
                QEvent.Type.Resize,
                QEvent.Type.WindowStateChange,
                QEvent.Type.Expose,
            }:
                QTimer.singleShot(0, self._sync_overlay_geometry)
            elif event_type == QEvent.Type.Close and not self._closing:
                self._closing = True
                self.overlay.close()

        elif watched is self.overlay:
            if event_type in {QEvent.Type.Show, QEvent.Type.Resize}:
                QTimer.singleShot(0, self._sync_overlay_geometry)
            elif event_type == QEvent.Type.Close and not self._closing:
                self._closing = True
                self.owner.close()

        return False


def install_native_window_shell(overlay: QMainWindow, owner: QQuickWindow) -> NativeWindowShell:
    shell = NativeWindowShell(overlay, owner)
    overlay._native_window_shell = shell  # type: ignore[attr-defined]
    return shell
