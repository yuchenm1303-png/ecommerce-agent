from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QMainWindow


_GWLP_HWNDPARENT = -8
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010


def _set_native_owner(overlay_hwnd: int, owner_hwnd: int) -> None:
    if sys.platform != "win32" or not overlay_hwnd or not owner_hwnd:
        return
    user32 = ctypes.windll.user32
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    set_long.restype = ctypes.c_ssize_t
    set_long(ctypes.c_void_p(overlay_hwnd), _GWLP_HWNDPARENT, ctypes.c_ssize_t(owner_hwnd))


def _client_geometry(hwnd: int) -> QRect:
    if sys.platform != "win32" or not hwnd:
        return QRect()

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    user32 = ctypes.windll.user32
    rect = RECT()
    origin = POINT(0, 0)
    if not user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        return QRect()
    if not user32.ClientToScreen(ctypes.c_void_p(hwnd), ctypes.byref(origin)):
        return QRect()
    return QRect(
        int(origin.x),
        int(origin.y),
        max(1, int(rect.right - rect.left)),
        max(1, int(rect.bottom - rect.top)),
    )


def _stack_owner_directly_behind(owner_hwnd: int, overlay_hwnd: int) -> None:
    if sys.platform != "win32" or not owner_hwnd or not overlay_hwnd:
        return
    ctypes.windll.user32.SetWindowPos(
        ctypes.c_void_p(owner_hwnd),
        ctypes.c_void_p(overlay_hwnd),
        0,
        0,
        0,
        0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
    )


class NativeWindowShell(QObject):
    """Native Windows frame on Quick, with the existing QWidget GUI as its owner-bound overlay."""

    def __init__(self, overlay: QMainWindow, owner: QQuickWindow) -> None:
        super().__init__(overlay)
        self.overlay = overlay
        self.owner = owner
        self._closing = False

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

        # The QWidget layer needs per-pixel alpha, so only this owned overlay is
        # frameless. The actual application window is the framed QQuickWindow.
        overlay.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        overlay.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        owner.installEventFilter(self)
        overlay.installEventFilter(self)

    def show(self) -> None:
        self.owner.create()
        self.overlay.winId()
        overlay_handle = self.overlay.windowHandle()
        if overlay_handle is None:
            raise RuntimeError("QWidget overlay has no native window handle")

        overlay_handle.setTransientParent(self.owner)
        _set_native_owner(int(self.overlay.winId()), int(self.owner.winId()))

        self.owner.show()
        self._sync_overlay_geometry()
        self.overlay.show()
        self.overlay.raise_()
        self._restack_pair()
        QTimer.singleShot(0, self._sync_overlay_geometry)

    def _sync_overlay_geometry(self) -> None:
        if self._closing:
            return
        rect = _client_geometry(int(self.owner.winId()))
        if rect.isValid() and self.overlay.geometry() != rect:
            self.overlay.setGeometry(rect)

        minimized = bool(self.owner.windowState() & Qt.WindowState.WindowMinimized)
        if minimized:
            if self.overlay.isVisible():
                self.overlay.hide()
        elif self.owner.isVisible() and not self.overlay.isVisible():
            self.overlay.show()
            self.overlay.raise_()

    def _restack_pair(self) -> None:
        if self._closing or not self.owner.isVisible() or not self.overlay.isVisible():
            return
        _stack_owner_directly_behind(int(self.owner.winId()), int(self.overlay.winId()))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.owner:
            if event_type in {
                QEvent.Type.Move,
                QEvent.Type.Resize,
                QEvent.Type.Show,
                QEvent.Type.WindowStateChange,
                QEvent.Type.ActivationChange,
                QEvent.Type.WindowActivate,
                QEvent.Type.Expose,
            }:
                QTimer.singleShot(0, self._sync_overlay_geometry)
                QTimer.singleShot(0, self._restack_pair)
            elif event_type == QEvent.Type.Close and not self._closing:
                self._closing = True
                self.overlay.close()

        elif watched is self.overlay:
            if event_type in {
                QEvent.Type.Show,
                QEvent.Type.WindowActivate,
                QEvent.Type.ActivationChange,
                QEvent.Type.ZOrderChange,
            }:
                QTimer.singleShot(0, self._sync_overlay_geometry)
                QTimer.singleShot(0, self._restack_pair)
            elif event_type == QEvent.Type.Close and not self._closing:
                self._closing = True
                self.owner.close()

        return False


def install_native_window_shell(overlay: QMainWindow, owner: QQuickWindow) -> NativeWindowShell:
    shell = NativeWindowShell(overlay, owner)
    overlay._native_window_shell = shell  # type: ignore[attr-defined]
    return shell
