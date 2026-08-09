from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QToolButton,
    QWidget,
)


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

    # COLORREF is 0x00BBGGRR.
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


class _WindowTitleBar(QWidget):
    """Windows-style caption for the required translucent frameless Qt window."""

    _HEIGHT = 36

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("nativeWindowTitleBar")
        self.setFixedHeight(self._HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(0)

        self.title = QLabel(window.windowTitle())
        self.title.setObjectName("nativeWindowTitle")
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.title, 1)

        self.minimize_button = self._make_button("—", "nativeWindowMinimize")
        self.maximize_button = self._make_button("□", "nativeWindowMaximize")
        self.close_button = self._make_button("×", "nativeWindowClose")

        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self._toggle_maximize)
        self.close_button.clicked.connect(window.close)

        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

        self.setStyleSheet(
            """
            QWidget#nativeWindowTitleBar {
                background: rgba(10, 18, 30, 210);
                border: 0;
                border-bottom: 1px solid rgba(255,255,255,45);
            }
            QLabel#nativeWindowTitle {
                color: rgba(255,255,255,225);
                background: transparent;
                border: 0;
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 12px;
                font-weight: 600;
            }
            QToolButton#nativeWindowMinimize,
            QToolButton#nativeWindowMaximize,
            QToolButton#nativeWindowClose {
                min-width: 46px;
                max-width: 46px;
                min-height: 36px;
                max-height: 36px;
                padding: 0;
                margin: 0;
                border: 0;
                border-radius: 0;
                color: rgba(255,255,255,235);
                background: transparent;
                font-family: "Segoe UI Symbol", "Segoe UI", sans-serif;
                font-size: 16px;
                font-weight: 400;
            }
            QToolButton#nativeWindowMinimize:hover,
            QToolButton#nativeWindowMaximize:hover {
                background: rgba(255,255,255,28);
            }
            QToolButton#nativeWindowMinimize:pressed,
            QToolButton#nativeWindowMaximize:pressed {
                background: rgba(255,255,255,45);
            }
            QToolButton#nativeWindowClose:hover {
                background: rgb(196, 43, 28);
                color: white;
            }
            QToolButton#nativeWindowClose:pressed {
                background: rgb(155, 34, 22);
                color: white;
            }
            """
        )
        self.sync_state()

    def _make_button(self, text: str, object_name: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName(object_name)
        button.setText(text)
        button.setAutoRaise(False)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _toggle_maximize(self) -> None:
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()
        QTimer.singleShot(0, self.sync_state)

    def sync_state(self) -> None:
        self.title.setText(self.window.windowTitle())
        self.maximize_button.setText("❐" if self.window.isMaximized() else "□")
        self.maximize_button.setToolTip("还原" if self.window.isMaximized() else "最大化")
        self.minimize_button.setToolTip("最小化")
        self.close_button.setToolTip("关闭")

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _ClientFrame(QWidget):
    """Visible full-window resize/frame fallback around the custom caption."""

    _EDGE = 5

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sync_geometry()

    def sync_geometry(self) -> None:
        self.setGeometry(self.window.rect())
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
        outer = QColor(8, 16, 28, 245 if active else 205)
        keyline = QColor(225, 242, 255, 180 if active else 105)
        shadow = QColor(0, 0, 0, 125 if active else 85)

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
    """Desktop shell for the two-surface GUI.

    The Quick scene graph remains responsible only for wallpaper/glass. This
    object makes the frameless translucent QWidget behave like a normal desktop
    application: caption controls, dragging, maximize/restore, a visible frame,
    and continuous adjacency between the QWidget and QQuickWindow HWNDs.
    """

    _Z_GUARD_MS = 50

    def __init__(self, window: QMainWindow, quick_window: QQuickWindow | None) -> None:
        super().__init__(window)
        self.window = window
        self.quick_window = quick_window

        self.title_bar = _WindowTitleBar(window)
        window.setMenuWidget(self.title_bar)

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
        self.title_bar.sync_state()
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
            QEvent.Type.WindowTitleChange,
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
