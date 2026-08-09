from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL_PATH = ROOT / "gui" / "native_window_shell.py"
SHELL = SHELL_PATH.read_text(encoding="utf-8")
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
VISUAL = (ROOT / "gui" / "visual_style.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_native_window_shell_source_compiles_without_importing_pyside() -> None:
    compile(SHELL, str(SHELL_PATH), "exec")


def test_quick_owner_has_real_windows_non_client_frame() -> None:
    assert "Qt.WindowType.WindowTitleHint" in NATIVE
    assert "Qt.WindowType.WindowSystemMenuHint" in NATIVE
    assert "Qt.WindowType.WindowMinMaxButtonsHint" in NATIVE
    assert "Qt.WindowType.WindowCloseButtonHint" in NATIVE
    assert "_WindowTitleBar" not in SHELL + VISUAL
    assert "setMenuWidget" not in SHELL + VISUAL


def test_widget_gui_is_a_layered_native_child_not_second_top_level() -> None:
    assert "overlay_handle.setParent(self.owner)" in SHELL
    assert "_embed_native_child" in SHELL
    assert "_WS_CHILD" in SHELL
    assert "_WS_EX_LAYERED" in SHELL
    assert "_WS_EX_APPWINDOW" in SHELL
    assert "_WS_EX_TOOLWINDOW" in SHELL
    assert "SetParent" in SHELL
    assert "GetParent" in SHELL
    assert "setTransientParent" not in SHELL
    assert "WindowTransparentForInput" not in SHELL
    assert "WA_TransparentForMouseEvents" not in SHELL


def test_overlay_geometry_uses_one_native_pixel_boundary() -> None:
    assert "_fit_child_to_owner_client" in SHELL
    assert "GetClientRect" in SHELL
    assert "SetWindowPos" in SHELL
    assert "width = max(1, int(rect.right - rect.left))" in SHELL
    assert "height = max(1, int(rect.bottom - rect.top))" in SHELL

    fit_body = SHELL.split("def _fit_native_child", 1)[1].split("def eventFilter", 1)[0]
    assert "overlay.setGeometry" not in fit_body
    assert "handle.resize" not in fit_body
    assert "handle.setPosition" not in fit_body
    assert "owner.width()" not in fit_body
    assert "owner.height()" not in fit_body
    assert "ClientToScreen" not in SHELL
    assert "_stack_owner_directly_behind" not in SHELL
    assert "setInterval(50)" not in SHELL
    assert "_Z_GUARD_MS" not in SHELL


def test_no_window_container_or_second_background_child_path() -> None:
    assert "createWindowContainer" not in NATIVE + SHELL + VISUAL
    assert "QQuickWidget" not in NATIVE + VISUAL
    assert "self.quick_window.setParent(" not in NATIVE


def test_runner_creates_renderer_then_child_shell() -> None:
    visual_pos = RUNNER.index("visual = install_visual_style(window)")
    shell_pos = RUNNER.index("shell = install_native_window_shell(window, quick_window)")
    assert visual_pos < shell_pos
    assert "shell.show()" in RUNNER
    assert "window.show()" not in RUNNER
