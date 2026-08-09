from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL_PATH = ROOT / "gui" / "native_window_shell.py"
SHELL = SHELL_PATH.read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_native_window_shell_source_compiles_without_importing_pyside() -> None:
    compile(SHELL, str(SHELL_PATH), "exec")


def test_shell_keeps_quick_window_immediately_behind_main_window() -> None:
    assert "SetWindowPos" in SHELL
    assert "_stack_immediately_behind" in SHELL
    assert "_Z_GUARD_MS = 50" in SHELL
    assert "QEvent.Type.WindowDeactivate" in SHELL
    assert "QEvent.Type.ZOrderChange" in SHELL
    assert "_z_guard.timeout.connect(self._guard_z_order)" in SHELL


def test_z_guard_is_not_a_wallpaper_animation_timer() -> None:
    assert "FrameAnimation" not in SHELL
    assert "setInterval(16)" not in SHELL


def test_shell_has_real_caption_controls_and_drag_behavior() -> None:
    assert "class _WindowTitleBar" in SHELL
    assert 'self._make_button("—", "nativeWindowMinimize")' in SHELL
    assert 'self._make_button("□", "nativeWindowMaximize")' in SHELL
    assert 'self._make_button("×", "nativeWindowClose")' in SHELL
    assert "self.minimize_button.clicked.connect(window.showMinimized)" in SHELL
    assert "self.maximize_button.clicked.connect(self._toggle_maximize)" in SHELL
    assert "self.close_button.clicked.connect(window.close)" in SHELL
    assert "handle.startSystemMove()" in SHELL
    assert "mouseDoubleClickEvent" in SHELL
    assert "window.setMenuWidget(self.title_bar)" in SHELL


def test_shell_has_native_and_widget_frame_fallbacks() -> None:
    assert "DWMWA_BORDER_COLOR" in SHELL
    assert "DwmSetWindowAttribute" in SHELL
    assert "class _ClientFrame" in SHELL
    assert "_EDGE = 5" in SHELL
    assert "legacy_frame.hide()" in SHELL


def test_runner_installs_shell_after_visual_background_exists() -> None:
    visual_pos = RUNNER.index("visual = install_visual_style(window)")
    shell_pos = RUNNER.index("install_native_window_shell(")
    assert visual_pos < shell_pos
    assert 'getattr(visual.background, "quick_window", None)' in RUNNER
