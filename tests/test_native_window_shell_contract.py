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


def test_widget_gui_is_owned_translucent_overlay_not_application_frame() -> None:
    assert "Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint" in SHELL
    assert "overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)" in SHELL
    assert "overlay_handle.setTransientParent(self.owner)" in SHELL
    assert "_GWLP_HWNDPARENT" in SHELL
    assert "_set_native_owner" in SHELL
    assert "WindowTransparentForInput" not in SHELL
    assert "WA_TransparentForMouseEvents" not in SHELL


def test_owner_and_overlay_are_kept_as_one_windows_instance() -> None:
    assert "_client_geometry" in SHELL
    assert "GetClientRect" in SHELL
    assert "ClientToScreen" in SHELL
    assert "_stack_owner_directly_behind" in SHELL
    assert "setInterval(50)" not in SHELL
    assert "_Z_GUARD_MS" not in SHELL


def test_no_window_container_or_child_embedding_path_remains() -> None:
    assert "createWindowContainer" not in NATIVE + SHELL + VISUAL
    assert "setParent(host_window)" not in NATIVE
    assert "_WS_EX_LAYERED" not in SHELL
    assert "_WS_CHILD" not in SHELL
    assert "QQuickWidget" not in NATIVE + VISUAL


def test_runner_creates_renderer_then_owner_bound_shell() -> None:
    visual_pos = RUNNER.index("visual = install_visual_style(window)")
    shell_pos = RUNNER.index("shell = install_native_window_shell(window, quick_window)")
    assert visual_pos < shell_pos
    assert "shell.show()" in RUNNER
    assert "window.show()" not in RUNNER
