from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "native_window_shell.py").read_text(encoding="utf-8")


def test_formal_window_first_show_is_maximized_not_exclusive_fullscreen() -> None:
    assert "self.owner.showMaximized()" in SOURCE
    assert "self.owner.showFullScreen()" not in SOURCE


def test_native_child_is_fitted_after_maximized_owner_show() -> None:
    show_index = SOURCE.index("self.owner.showMaximized()")
    fit_index = SOURCE.index("self._fit_native_child()", show_index)
    overlay_index = SOURCE.index("self.overlay.show()", fit_index)
    assert show_index < fit_index < overlay_index


def test_native_focus_transfer_is_idempotent() -> None:
    focus_start = SOURCE.index("def _focus_native_child(")
    keyboard_start = SOURCE.index("_KEYBOARD_WIDGET_TYPES", focus_start)
    focus_source = SOURCE[focus_start:keyboard_start]

    assert "if int(user32.GetFocus() or 0) == overlay_hwnd:" in focus_source
    assert "user32.SetFocus(overlay)" in focus_source


def test_qt_child_focus_does_not_feed_back_into_native_focus_transfer() -> None:
    changed_start = SOURCE.index("def _on_focus_changed(")
    schedule_start = SOURCE.index("def _schedule_widget_focus(", changed_start)
    changed_source = SOURCE[changed_start:schedule_start]

    overlay_branch_start = SOURCE.index("elif watched is self.overlay:")
    keyboard_branch_start = SOURCE.index(
        "elif isinstance(watched, _KEYBOARD_WIDGET_TYPES):",
        overlay_branch_start,
    )
    overlay_source = SOURCE[overlay_branch_start:keyboard_branch_start]

    assert "self._last_focus_widget = current" in changed_source
    assert "self._schedule_widget_focus()" not in changed_source
    assert "QEvent.Type.FocusIn" not in overlay_source


def test_native_window_shell_source_compiles() -> None:
    compile(SOURCE, str(ROOT / "gui" / "native_window_shell.py"), "exec")
