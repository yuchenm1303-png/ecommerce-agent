from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QMainWindow, QWidget


_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_NOSENDCHANGING = 0x0400
_DWMWA_BORDER_COLOR = 34


def _stack_immediately_behind(background_wid: int, foreground_wid: int) -> None:
    """Keep two top-level HWNDs adjacent without moving/activating either one."""

    if sys.platform != "win32" or not background_wid or not foreground_wid:
        return
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = ctypes.c_int
    user32.SetWindowPos(
        ctypes.c_void_p(background_wid),
        ctypes.c_void_p(foreground_wid),
        0,
        0,
        0,
        0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_NOSENDCHANGING,
    )


def _set_dwm_border(hwnd: int, *, active: bool) -> None:
    """Ask Windows 11 DWM to draw a native outer keyline when supported."""

    if sys.platform != "win32" or not hwnd:
        return
    try:
        dwmapi = ctypes.windll.dwmapi
    except AttributeError:
        return

    # COLORREF is 0x00BBGGRR. Use a cool light edge while active and a darker
    # neutral edge while inactive; the QWidget fallback below is still present.
    colorref = ctypes.c_uint32(0x00F0D8B8 if active else 0x00605040)
    try:
        dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(_DWMWA_BORDER_COLOR),
            ctypes.byref(colorref),
            ctypes.sizeof(colorref),
        )
    except (AttributeError, OSError):
        return


class _ClientFrame(QWidget):
    """Visible in-client frame for the required translucent frameless shell."""

    _EDGE = 6

    def __init__(self, window: QMainWindow) -> None:
        host = window.centralWidget() or window
        super().__init__(host)
        self.window = window
        self.host = host
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sync_geometry()

    def sync_geometry(self) -> None:
        self.setGeometry(self.host.rect())
        self.raise_()
        self.show()
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        if self.width() < 4 or self.height() < 4:
            return

        maximized = self.window.isMaximized()
        edge = 1 if maximized else self._EDGE
        active = self.window.isActiveWindow()
        outer = QColor(8, 16, 28, 235 if active else 190)
        keyline = QColor(220, 240, 255, 190 if active else 105)
        shadow = QColor(0, 0, 0, 115 if active else 80)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(outer)
        painter.drawRect(0, 0, rect.width(), edge)
        painter.drawRect(0, rect.height() - edge, rect.width(), edge)
        painter.drawRect(0, 0, edge, rect.height())
        painter.drawRect(rect.width() - edge, 0, edge, rect.height())

        if not maximized and rect.width() > 16 and rect.height() > 16:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(keyline, 1.0))
            painter.drawRect(rect.adjusted(edge, edge, -edge - 1, -edge - 1))
            painter.setPen(QPen(shadow, 1.0))
            painter.drawRect(rect.adjusted(edge + 1, edge + 1, -edge - 2, -edge - 2))
        painter.end()


class NativeWindowShell(QObject):
    """Windows-only shell guard for the two-surface GUI.

    The Quick scene graph remains responsible only for wallpaper/glass. This
    object handles desktop-window concerns: an unmistakable frame and keeping
    the native Quick surface directly behind the translucent QWidget window.
    """

    _Z_GUARD_MS = 50

    def __init__(self, window: QMainWindow, quick_window: QQuickWindow | None) -> None:
        super().__init__(window)
        self.window = window
        self.quick_window = quick_window
        self.frame = _ClientFrame(window)

        self._z_guard = QTimer(self)
        self._z_guard.setInterval(self._Z_GUARD_MS)
        self._z_guard.setTimerType(Qt.TimerType.CoarseTimer)
        self._z_guard.timeout.connect(self._guard_z_order)

        window.installEventFilter(self)
        QTimer.singleShot(0, self._sync_shell)

    def _guard_z_order(self) -> None:
        quick = self.quick_window
        if (
            quick is None
            or not quick.isVisible()
            or not self.window.isVisible()
            or self.window.isMinimized()
        ):
            return
        _stack_immediately_behind(int(quick.winId()), int(self.window.winId()))

    def _sync_shell(self) -> None:
        self.frame.sync_geometry()
        _set_dwm_border(int(self.window.winId()), active=self.window.isActiveWindow())
        self._guard_z_order()
        if self.window.isVisible() and not self.window.isMinimized():
            if not self._z_guard.isActive():
                self._z_guard.start()
        else:
            self._z_guard.stop()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self.window:
            return False
        if event.type() in {
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.Resize,
            QEvent.Type.Move,
            QEvent.Type.WindowStateChange,
            QEvent.Type.WindowActivate,
            QEvent.Type.WindowDeactivate,
            QEvent.Type.ActivationChange,
            QEvent.Type.ZOrderChange,
        }:
            QTimer.singleShot(0, self._sync_shell)
        if event.type() == QEvent.Type.Close:
            self._z_guard.stop()
        return False


def install_native_window_shell(
    window: QMainWindow,
    quick_window: QQuickWindow | None,
    *,
    legacy_frame: QWidget | None = None,
) -> NativeWindowShell:
    if legacy_frame is not None:
        legacy_frame.hide()
    shell = NativeWindowShell(window, quick_window)
    window._native_window_shell = shell  # type: ignore[attr-defined]
    return shell
