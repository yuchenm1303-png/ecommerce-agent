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


def test_native_window_shell_source_compiles() -> None:
    compile(SOURCE, str(ROOT / "gui" / "native_window_shell.py"), "exec")
