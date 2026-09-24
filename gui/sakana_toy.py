from __future__ import annotations

import subprocess
import sys
import ctypes
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.runtime_paths import is_frozen, runtime_root


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SAKANA_HELPER_EXE = "EcommerceAgentSakana.exe"
_SAKANA_DEV_EXE = (
    _REPO_ROOT
    / "native"
    / "sakana-helper"
    / "bin"
    / "Release"
    / _SAKANA_HELPER_EXE
)
_SAKANA_DEV_ASSETS = _REPO_ROOT / "native" / "sakana-helper" / "assets"
_VISIBILITY_MESSAGE = "EcommerceAgent.Sakana.SetVisible.v1"


class SakanaToyController(QObject):
    """Start/stop the fully isolated Sakana browser process.

    The listing GUI owns no Sakana renderer, WebEngine surface, animation clock,
    spring state, geometry polling or presentation callback. The child receives
    only the native QQuickWindow handle and follows that window itself.

    The owner must be the QQuickWindow background layer, not the QMainWindow:
    `window.winId()` resolves to a hidden 1x1 placeholder handle in this app's
    window setup (verified with GetWindowRect: 1x1 at (-21333,-21333),
    IsWindowVisible=False), not the visible top-level surface the user actually
    sees and interacts with. That visible surface is the QQuickWindow.
    """

    def __init__(self, window: QWidget, quick: QQuickWindow) -> None:
        super().__init__(window)
        self.window = window
        self.quick = quick
        self._enabled = False
        self._shutting_down = False
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle = None
        self._launch_attempts = 0

        self._launch_timer = QTimer(self)
        self._launch_timer.setSingleShot(True)
        self._launch_timer.timeout.connect(self._start_process)
        self._health_timer = QTimer(self)
        self._health_timer.setSingleShot(True)
        self._health_timer.timeout.connect(self._verify_process)

        self.toggle = self._install_toggle()
        self.window.destroyed.connect(self.cleanup)
        # Do not create the helper while the native shell is still being assembled.
        # run_local_gui calls raise_overlay() immediately after shell.show(), which
        # is the first point where the QQuickWindow HWND has stable native geometry.

    def _install_toggle(self) -> QPushButton:
        root = self.window.centralWidget()
        outer = root.layout() if root is not None else None
        if not isinstance(outer, QVBoxLayout) or outer.count() < 1:
            raise RuntimeError("Sakana toy expected the preserved application root layout")
        header = outer.itemAt(0).layout()
        if not isinstance(header, QHBoxLayout):
            raise RuntimeError("Sakana toy expected the common application header")

        button = QPushButton("玩具 · OFF")
        button.setObjectName("quietButton")
        button.setCheckable(True)
        button.setChecked(False)
        button.setMinimumWidth(98)
        button.setToolTip("显示或隐藏左下角弹簧玩具")
        button.setStyleSheet(
            "QPushButton:checked {"
            "background-color: rgba(190, 113, 157, 150);"
            "border-color: rgba(255, 220, 239, 90);"
            "font-weight: 700;"
            "}"
        )
        button.toggled.connect(self.set_enabled)
        header.addWidget(button, 0, Qt.AlignmentFlag.AlignBottom)
        return button

    def _owner_hwnd(self) -> int:
        try:
            hwnd = int(self.quick.winId())
        except RuntimeError as exc:
            raise RuntimeError("Sakana toy could not resolve the Quick native window") from exc
        if hwnd <= 0:
            raise RuntimeError("Sakana toy received an invalid Quick native window handle")
        return hwnd

    def _child_command(self) -> list[str]:
        owner_hwnd = str(self._owner_hwnd())
        if is_frozen():
            bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            helper = bundle_root / "native" / "sakana" / "helper" / _SAKANA_HELPER_EXE
            asset_dir = bundle_root / "native" / "sakana" / "assets"
            if not helper.is_file():
                raise RuntimeError(f"Sakana helper executable is missing: {helper}")
        else:
            helper = _SAKANA_DEV_EXE
            asset_dir = _SAKANA_DEV_ASSETS
        if not helper.is_file():
            raise RuntimeError(f"Build the native Sakana helper first: {helper}")
        if not (asset_dir / "index.html").is_file():
            raise RuntimeError(f"Sakana static assets are missing: {asset_dir}")
        command = [
            str(helper),
            "--owner-hwnd", owner_hwnd,
            "--asset-dir", str(asset_dir),
            "--log", str(runtime_root() / "logs" / "sakana-helper.log"),
        ]
        if not self._enabled:
            command.append("--start-hidden")
        return command

    def _set_process_visible(self, visible: bool) -> bool:
        process = self._process
        if sys.platform != "win32" or process is None or process.poll() is not None:
            return False
        user32 = ctypes.windll.user32
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        message = user32.RegisterWindowMessageW(_VISIBILITY_MESSAGE)
        found = False
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def visit(hwnd: int, _context: int) -> bool:
            nonlocal found
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == process.pid:
                user32.PostMessageW(hwnd, message, int(visible), 0)
                found = True
            return True

        user32.EnumWindows(callback_type(visit), 0)
        return found

    def _start_process(self) -> None:
        if self._shutting_down:
            return
        current = self._process
        if current is not None and current.poll() is None:
            if self._enabled:
                self._set_process_visible(True)
            return

        creationflags = 0
        if sys.platform == "win32":
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

        log_path = runtime_root() / "logs" / "sakana-launcher.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if self._log_handle is not None:
            self._log_handle.close()
        self._log_handle = log_path.open("ab")
        self._process = subprocess.Popen(
            self._child_command(),
            cwd=str(_REPO_ROOT) if not is_frozen() else None,
            stdin=subprocess.DEVNULL,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True,
        )
        self._launch_attempts += 1
        self._health_timer.start(700)

    def _verify_process(self) -> None:
        if self._shutting_down:
            return
        process = self._process
        if process is not None and process.poll() is None:
            if self._enabled:
                self._set_process_visible(True)
            return
        self._process = None
        # During startup Qt can replace the first native QQuickWindow handle.
        # Retry with a freshly resolved HWND after the event loop settles.
        if self._launch_attempts < 3:
            self._launch_timer.start(250)

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            return

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self._enabled = enabled
        if self.toggle.isChecked() != enabled:
            self.toggle.blockSignals(True)
            self.toggle.setChecked(enabled)
            self.toggle.blockSignals(False)
        self.toggle.setText("玩具 · ON" if enabled else "玩具 · OFF")

        if enabled:
            self._launch_attempts = 0
            # The window is already stable when this user-driven toggle fires.
            # Queue startup for the next event-loop turn so the button repaints,
            # but do not add an artificial delay before the cold helper launch.
            if not self._set_process_visible(True):
                self._launch_timer.start(0)
        else:
            self._launch_timer.stop()
            self._health_timer.stop()
            # Keep the prepared WebView2 process alive for instant subsequent
            # toggles. Development changes are picked up when the GUI restarts.
            self._set_process_visible(False)

    def raise_overlay(self) -> None:
        # shell.show() does not synchronously stabilize the QQuickWindow HWND.
        # Launch on a later event-loop turn and retry if that transient owner dies.
        self._launch_attempts = 0
        self._launch_timer.start(0)

    def cleanup(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._launch_timer.stop()
        self._health_timer.stop()
        self._stop_process()
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


def install_sakana_toy(window: QWidget) -> SakanaToyController:
    existing = getattr(window, "_sakana_toy_controller", None)
    if isinstance(existing, SakanaToyController):
        return existing

    visual = getattr(window, "_visual_style", None)
    background = getattr(visual, "background", None)
    quick = getattr(background, "quick_window", None)
    if not isinstance(quick, QQuickWindow):
        raise RuntimeError("Sakana toy requires the existing unified QQuickWindow")

    controller = SakanaToyController(window, quick)
    window._sakana_toy_controller = controller  # type: ignore[attr-defined]
    return controller
