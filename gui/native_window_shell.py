from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget


_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_WS_EX_LAYERED = 0x00080000
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_TOOLWINDOW = 0x00000080
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


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
        _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
    )

    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetParent.restype = ctypes.c_void_p
    actual_parent = int(user32.GetParent(overlay) or 0)
    if actual_parent != owner_hwnd:
        raise RuntimeError("Baseline QWidget surface was not embedded under Quick")


def _fit_child_to_owner_client(overlay_hwnd: int, owner_hwnd: int) -> None:
    """Keep the child HWND exactly equal to the native owner client rect.

    The boundary is deliberately native-pixel only. Feeding the result back
    through QWidget/QWindow logical geometry causes a second 150% DPI scale on
    the real Windows acceptance machine.
    """

    if sys.platform != "win32" or not overlay_hwnd or not owner_hwnd:
        return

    user32 = ctypes.windll.user32
    owner = ctypes.c_void_p(owner_hwnd)
    overlay = ctypes.c_void_p(overlay_hwnd)
    rect = _RECT()

    user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
    user32.GetClientRect.restype = ctypes.c_int
    if not user32.GetClientRect(owner, ctypes.byref(rect)):
        raise OSError("GetClientRect failed for native Quick owner")

    width = max(1, int(rect.right - rect.left))
    height = max(1, int(rect.bottom - rect.top))

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
    if not user32.SetWindowPos(
        overlay,
        None,
        0,
        0,
        width,
        height,
        _SWP_NOZORDER | _SWP_NOACTIVATE,
    ):
        raise OSError("SetWindowPos failed while fitting baseline QWidget child")


def _focus_native_child(overlay_hwnd: int) -> bool:
    """Give the QWidget child HWND the Win32 keyboard focus.

    Qt still chooses the concrete focusWidget (QLineEdit/QSpinBox/etc.). This
    only makes the native QWidget surface the recipient of keyboard messages
    instead of leaving them intermittently on the QQuickWindow owner.
    """

    if sys.platform != "win32" or not overlay_hwnd:
        return True

    user32 = ctypes.windll.user32
    overlay = ctypes.c_void_p(overlay_hwnd)
    user32.SetFocus.argtypes = [ctypes.c_void_p]
    user32.SetFocus.restype = ctypes.c_void_p
    user32.GetFocus.argtypes = []
    user32.GetFocus.restype = ctypes.c_void_p
    user32.SetFocus(overlay)
    return int(user32.GetFocus() or 0) == overlay_hwnd


class NativeWindowShell(QObject):
    """Native Windows frame with the unchanged baseline QWidget tree inside."""

    def __init__(self, overlay: QMainWindow, owner: QQuickWindow) -> None:
        super().__init__(overlay)
        self.overlay = overlay
        self.owner = owner
        self._closing = False
        self._embedded = False
        self._focus_pending = False
        self._last_focus_widget: QWidget | None = None
        self._focus_watch: list[QWidget] = []

        owner.setTitle(overlay.windowTitle())
        owner.setFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        owner.resize(overlay.size())
        owner.setMinimumSize(overlay.minimumSize())

        overlay.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        overlay.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        owner.installEventFilter(self)
        overlay.installEventFilter(self)
        owner.widthChanged.connect(self._schedule_native_fit)
        owner.heightChanged.connect(self._schedule_native_fit)

        # Mouse/focus events are delivered to the actual QWidget child control,
        # not necessarily QMainWindow. Watch the existing baseline widget tree so
        # every editable control can deterministically bridge native focus back
        # from QQuickWindow after click, first-show or Alt+Tab activation.
        self._focus_watch = [overlay, *overlay.findChildren(QWidget)]
        for widget in self._focus_watch:
            if widget is not overlay:
                widget.installEventFilter(self)

    def show(self) -> None:
        self.owner.create()
        self.overlay.winId()
        overlay_handle = self.overlay.windowHandle()
        if overlay_handle is None:
            raise RuntimeError("Baseline QWidget overlay has no native handle")

        overlay_handle.setParent(self.owner)
        _embed_native_child(int(self.overlay.winId()), int(self.owner.winId()))
        self._embedded = True

        self.owner.show()
        self._fit_native_child()
        self.overlay.show()
        QTimer.singleShot(0, self._fit_native_child)
        QTimer.singleShot(0, self._restore_widget_focus)

    def _schedule_native_fit(self, *_args: object) -> None:
        if self._closing or not self._embedded:
            return
        QTimer.singleShot(0, self._fit_native_child)

    def _fit_native_child(self) -> None:
        if self._closing or not self._embedded:
            return
        _fit_child_to_owner_client(int(self.overlay.winId()), int(self.owner.winId()))

    def _belongs_to_overlay(self, widget: QWidget | None) -> bool:
        current = widget
        while current is not None:
            if current is self.overlay:
                return True
            current = current.parentWidget()
        return False

    def _schedule_widget_focus(self) -> None:
        if self._closing or not self._embedded or self._focus_pending:
            return
        self._focus_pending = True
        QTimer.singleShot(0, self._restore_widget_focus)

    def _restore_widget_focus(self) -> None:
        self._focus_pending = False
        if self._closing or not self._embedded:
            return

        _focus_native_child(int(self.overlay.winId()))

        # Preserve Qt's per-control focus. If activation temporarily cleared it,
        # restore the last valid editable/control widget instead of hard-coding
        # the supplier URL field.
        current = QApplication.focusWidget()
        if self._belongs_to_overlay(current):
            self._last_focus_widget = current
            return

        target = self._last_focus_widget
        if target is not None and target.isVisible() and target.isEnabled():
            target.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.owner:
            if event_type in {
                QEvent.Type.Show,
                QEvent.Type.Resize,
                QEvent.Type.WindowStateChange,
                QEvent.Type.Expose,
            }:
                self._schedule_native_fit()
            if event_type in {
                QEvent.Type.Show,
                QEvent.Type.WindowActivate,
                QEvent.Type.FocusIn,
            }:
                self._schedule_widget_focus()
            elif event_type == QEvent.Type.Close and not self._closing:
                self._closing = True
                self.overlay.close()

        elif isinstance(watched, QWidget) and self._belongs_to_overlay(watched):
            if event_type == QEvent.Type.FocusIn:
                self._last_focus_widget = watched
                self._schedule_widget_focus()
            elif event_type == QEvent.Type.MouseButtonPress:
                # Run after Qt processes the click so its normal focus policy
                # first selects the concrete child control, then bridge the HWND.
                self._schedule_widget_focus()
            elif watched is self.overlay and event_type == QEvent.Type.Close and not self._closing:
                self._closing = True
                self.owner.close()

        return False


def install_native_window_shell(overlay: QMainWindow, owner: QQuickWindow) -> NativeWindowShell:
    shell = NativeWindowShell(overlay, owner)
    overlay._native_window_shell = shell  # type: ignore[attr-defined]
    return shell
