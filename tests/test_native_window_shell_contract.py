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


def test_top_level_keeps_the_real_windows_non_client_frame() -> None:
    assert "flags &= ~Qt.WindowType.FramelessWindowHint" in SHELL
    assert "Qt.WindowType.WindowTitleHint" in SHELL
    assert "Qt.WindowType.WindowSystemMenuHint" in SHELL
    assert "Qt.WindowType.WindowMinMaxButtonsHint" in SHELL
    assert "Qt.WindowType.WindowCloseButtonHint" in SHELL
    assert "_WindowTitleBar" not in SHELL
    assert "setMenuWidget" not in SHELL
    assert "nativeWindowMinimize" not in SHELL
    assert "nativeWindowMaximize" not in SHELL
    assert "nativeWindowClose" not in SHELL


def test_only_child_content_is_translucent_and_native() -> None:
    assert "window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)" in SHELL
    assert "content.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)" in SHELL
    assert "content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)" in SHELL
    assert "_WS_CHILD" in SHELL
    assert "_WS_EX_LAYERED" in SHELL
    assert "_ensure_layered_child" in SHELL
    assert "WS_EX_TRANSPARENT" not in SHELL


def test_quick_uses_qt_supported_native_window_container() -> None:
    assert "QWidget.createWindowContainer" in NATIVE
    assert "self.quick_window.setParent(host_window)" not in NATIVE
    assert "self.quick_container.setGeometry(surface_host.rect())" in NATIVE
    assert "self.quick_container.stackUnder(self.content_layer)" in NATIVE
    assert "_assert_same_native_parent" in NATIVE
    assert "_place_child_behind" in NATIVE
    assert "Qt.WindowType.Tool" not in NATIVE
    assert "QQuickWidget" not in NATIVE


def test_no_desktop_z_order_guard_or_custom_chrome_remains() -> None:
    assert "_Z_GUARD_MS" not in SHELL + NATIVE
    assert "setInterval(50)" not in SHELL + NATIVE
    assert "_stack_immediately_behind" not in SHELL + NATIVE
    assert "DWMWA_BORDER_COLOR" not in SHELL
    assert "DwmSetWindowAttribute" not in SHELL
    assert "class _ClientFrame" not in SHELL


def test_quick_is_input_transparent_but_business_content_is_not() -> None:
    assert "Qt.WindowType.WindowTransparentForInput" in NATIVE
    assert "self.quick_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)" in NATIVE
    assert "WindowTransparentForInput" not in SHELL
    assert "WA_TransparentForMouseEvents" not in SHELL


def test_runner_builds_native_host_before_visual_surfaces() -> None:
    shell_pos = RUNNER.index("shell = install_native_window_shell(window)")
    visual_pos = RUNNER.index("visual = install_visual_style(")
    assert shell_pos < visual_pos
    assert "content_root=shell.content_widget" in RUNNER
    assert "surface_host=shell.host_widget" in RUNNER


def test_visual_style_does_not_make_top_level_frameless_or_translucent() -> None:
    assert "window.setWindowFlag(Qt.WindowType.FramelessWindowHint" not in VISUAL
    assert "window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)" not in VISUAL
    assert "WindowFrameOverlay" not in VISUAL
