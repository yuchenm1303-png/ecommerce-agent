from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "startup_entrance.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_reference_timing_and_scale_tokens_are_preserved() -> None:
    for token in (
        "_LOADER_FADE_MS = 300",
        "_CURTAIN_DELAY_MS = 300",
        "_CURTAIN_MS = 500",
        "_BACKGROUND_DELAY_MS = 450",
        "_BACKGROUND_MS = 800",
        "_UI_SCALE_DELAY_MS = 500",
        "_UI_SCALE_MS = 650",
        "_TOTAL_MS = 1300",
        "_BG_START_SCALE = 1.60",
        "_UI_START_SCALE = 1.20",
        "_CURTAIN_FRACTION = 0.51",
        'QColor("#333333")',
    ):
        assert token in SOURCE


def test_reference_easing_curves_are_explicit() -> None:
    assert "_curve(0.645, 0.045, 0.355, 1.0)" in SOURCE
    assert "_curve(0.25, 0.46, 0.45, 0.94)" in SOURCE


def test_startup_is_one_snapshot_not_per_card_widget_animation() -> None:
    assert "central.render(" in SOURCE
    assert "self._ui_snapshot" in SOURCE
    assert "for record in self._glass_records:" in SOURCE
    assert "QPropertyAnimation" not in SOURCE
    assert "setGraphicsEffect" not in SOURCE


def test_startup_freezes_runtime_only_while_cover_is_visible() -> None:
    assert 'quick.setProperty("animationRunning", False)' in SOURCE
    assert 'quick.setProperty("offsetX", 0.0)' in SOURCE
    assert "suspend_for_modal" in SOURCE
    assert "resume_from_modal" in SOURCE
    assert "pointer_timer.stop()" in SOURCE
    assert "pointer_timer.start()" in SOURCE


def test_formal_launcher_covers_first_frame_then_starts_after_show() -> None:
    assert "from gui.startup_entrance import install_startup_entrance" in RUN
    assert "entrance = install_startup_entrance(window, visual)" in RUN
    assert "shell.show()" in RUN
    assert "entrance.raise_overlay()" in RUN
    assert "entrance.start()" in RUN
    assert RUN.index("entrance = install_startup_entrance(window, visual)") < RUN.index("shell.show()")
    assert RUN.index("shell.show()") < RUN.index("entrance.start()")


def test_startup_source_compiles_without_importing_pyside() -> None:
    compile(SOURCE, str(ROOT / "gui" / "startup_entrance.py"), "exec")
