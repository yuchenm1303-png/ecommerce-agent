from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.runtime_paths import is_frozen


_REPO_ROOT = Path(__file__).resolve().parents[1]


class SakanaToyController(QObject):
    """Start/stop the fully isolated Sakana browser process.

    The listing GUI owns no Sakana renderer, WebEngine surface, animation clock,
    spring state, geometry polling or presentation callback. The child receives
    only the native QQuickWindow handle and follows that window itself.
    """

    def __init__(self, window: QWidget, quick: QQuickWindow) -> None:
        super().__init__(window)
        self.window = window
        self.quick = quick
        self._enabled = True
        self._shutting_down = False
        self._process: subprocess.Popen[bytes] | None = None

        self.toggle = self._install_toggle()
        self.window.destroyed.connect(self.cleanup)
        self.set_enabled(True)

    def _install_toggle(self) -> QPushButton:
        root = self.window.centralWidget()
        outer = root.layout() if root is not None else None
        if not isinstance(outer, QVBoxLayout) or outer.count() < 1:
            raise RuntimeError("Sakana toy expected the preserved application root layout")
        header = outer.itemAt(0).layout()
        if not isinstance(header, QHBoxLayout):
            raise RuntimeError("Sakana toy expected the common application header")

        button = QPushButton("玩具 · ON")
        button.setObjectName("quietButton")
        button.setCheckable(True)
        button.setChecked(True)
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
            return [
                str(Path(sys.executable).resolve()),
                "--sakana-toy-process",
                "--owner-hwnd",
                owner_hwnd,
            ]
        return [
            str(Path(sys.executable).resolve()),
            "-m",
            "gui.sakana_process",
            "--owner-hwnd",
            owner_hwnd,
        ]

    def _start_process(self) -> None:
        if self._shutting_down or not self._enabled:
            return
        current = self._process
        if current is not None and current.poll() is None:
            return

        creationflags = 0
        if sys.platform == "win32":
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

        env = os.environ.copy()
        self._process = subprocess.Popen(
            self._child_command(),
            cwd=str(_REPO_ROOT) if not is_frozen() else None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )

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
            self._start_process()
        else:
            self._stop_process()

    def raise_overlay(self) -> None:
        # Native z-order and position belong to the child. This method only keeps
        # the existing startup call site compatible and can recover a dead child.
        if self._enabled:
            self._start_process()

    def cleanup(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._stop_process()


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
