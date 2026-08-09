from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QMainWindow, QWidget


_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_WS_EX_LAYERED = 0x00080000
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


def _ensure_layered_child(hwnd: int) -> None:
    """Make the QWidget content a real layered child of the native client host."""

    if sys.platform != "win32" or not hwnd:
        return

    user32, get_long, set_long = _window_long_functions()
    handle = ctypes.c_void_p(hwnd)

    style = int(get_long(handle, _GWL_STYLE))
    style = (style | _WS_CHILD) & ~_WS_POPUP
    set_long(handle, _GWL_STYLE, style)

    exstyle = int(get_long(handle, _GWL_EXSTYLE))
    set_long(handle, _GWL_EXSTYLE, exstyle | _WS_EX_LAYERED)

    user32.SetWindowPos(
        handle,
        None,
        0,
        0,
        0,
        0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
    )


class NativeWindowShell(QObject):
    """Keep Windows' real frame and move both visual surfaces into its client area.

    The QMainWindow remains one ordinary top-level Windows application window,
    so DWM owns the title bar, minimize/maximize/close buttons, resizing, Snap,
    Alt+Tab and taskbar behavior. The existing business QWidget tree becomes a
    layered native child; the QQuickWindow background is attached later as a
    second child underneath it. Other applications can therefore never be
    inserted between the business UI and its wallpaper.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window

        # Explicitly keep the normal Windows non-client frame. The previous
        # frameless/translucent top-level architecture is intentionally removed.
        flags = window.windowFlags()
        flags &= ~Qt.WindowType.FramelessWindowHint
        flags |= (
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        window.setWindowFlags(flags)
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)

        content = window.takeCentralWidget()
        if content is None:
            raise RuntimeError("Native window shell requires the existing GUI central widget")

        host = QWidget(window)
        host.setObjectName("nativeClientHost")
        host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        host.setAutoFillBackground(False)
        host.setStyleSheet("QWidget#nativeClientHost { background: #17263a; border: 0; }")
        window.setCentralWidget(host)

        content.setParent(host)
        content.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        content.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        content.setAutoFillBackground(False)
        content.setGeometry(host.rect())

        # Create both native handles before the Quick child is attached. Windows
        # 8+ supports WS_EX_LAYERED on child HWNDs, which lets this content layer
        # preserve its alpha without making the top-level application frameless.
        host.winId()
        content.winId()
        _ensure_layered_child(int(content.winId()))

        content.show()
        content.raise_()

        self.host_widget = host
        self.content_widget = content
        host.installEventFilter(self)

    def _sync_content_geometry(self) -> None:
        self.content_widget.setGeometry(self.host_widget.rect())
        self.content_widget.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.host_widget and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        }:
            self._sync_content_geometry()
        return False


def install_native_window_shell(window: QMainWindow) -> NativeWindowShell:
    shell = NativeWindowShell(window)
    window._native_window_shell = shell  # type: ignore[attr-defined]
    return shell
