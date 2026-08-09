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
    assert "wallpaper" not in SHELL.lower()
    assert "setInterval(16)" not in SHELL


def test_shell_has_native_and_widget_frame_fallbacks() -> None:
    assert "DWMWA_BORDER_COLOR" in SHELL
    assert "DwmSetWindowAttribute" in SHELL
    assert "class _ClientFrame" in SHELL
    assert "_EDGE = 6" in SHELL
    assert "legacy_frame.hide()" in SHELL


def test_runner_installs_shell_after_visual_background_exists() -> None:
    visual_pos = RUNNER.index("visual = install_visual_style(window)")
    shell_pos = RUNNER.index("install_native_window_shell(")
    assert visual_pos < shell_pos
    assert 'getattr(visual.background, "quick_window", None)' in RUNNER
