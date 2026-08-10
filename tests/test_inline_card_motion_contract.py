from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOTION = (ROOT / "gui" / "overlay_sheet_motion.py").read_text(encoding="utf-8")
SHEETS = (ROOT / "gui" / "anchored_sheets.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_runner_installs_anchored_sheets_after_final_layout_before_native_shell() -> None:
    assert "from gui.anchored_sheets import install_anchored_sheets" in RUNNER
    assert "install_ui_polish(window)" in RUNNER
    assert "install_mature_ui(window)" in RUNNER
    assert "install_anchored_sheets(window)" in RUNNER
    assert "install_native_window_shell(window, quick_window)" in RUNNER
    assert RUNNER.index("install_mature_ui(window)") < RUNNER.index("install_anchored_sheets(window)")
    assert RUNNER.index("install_anchored_sheets(window)") < RUNNER.index(
        "install_native_window_shell(window, quick_window)"
    )


def test_obsolete_inline_layout_animation_modules_stay_removed() -> None:
    assert not (ROOT / "gui" / "inline_card_motion.py").exists()
    assert not (ROOT / "gui" / "inline_motion_glass_guard.py").exists()


def test_overlay_motion_changes_only_absolute_clip_viewport() -> None:
    assert "class ClipSheetMotion(QObject)" in MOTION
    assert "QElapsedTimer" in MOTION
    assert "Qt.TimerType.PreciseTimer" in MOTION
    assert "_FRAME_MS = 8" in MOTION
    assert "self.content.resize(rect.size())" in MOTION
    assert "self.viewport.setGeometry" in MOTION
    assert "self.content.move" in MOTION
    assert "QPropertyAnimation" not in MOTION
    assert "QParallelAnimationGroup" not in MOTION
    assert "QGraphicsOpacityEffect" not in MOTION
    assert "setMinimumHeight" not in MOTION
    assert "setMaximumHeight" not in MOTION
    assert "setSizes(" not in MOTION


def test_real_and_console_details_are_reparented_into_fixed_size_sheets() -> None:
    assert "def _build_real_sheet" in SHEETS
    assert "def _build_console_sheet" in SHEETS
    assert 'widget.setParent(self.real_sheet)' in SHEETS
    assert 'unit.setParent(self.console_sheet)' in SHEETS
    assert 'tabs.setParent(self.console_sheet)' in SHEETS
    assert 'edge="top"' in SHEETS
    assert 'edge="bottom"' in SHEETS
    assert "setMinimumHeight" not in SHEETS
    assert "setMaximumHeight" not in SHEETS
    assert "setSizes(" not in SHEETS


def test_overlay_buttons_do_not_expose_checked_state_to_old_splitter_logic() -> None:
    assert "toggle.toggled.disconnect()" in SHEETS
    assert "toggle.setCheckable(False)" in SHEETS
    assert 'toggle.clicked.connect(lambda: self._set_sheet("real", not self._real_open))' in SHEETS
    assert 'toggle.clicked.connect(lambda: self._set_sheet("console", not self._console_open))' in SHEETS


def test_sheet_motion_is_distance_free_and_text_never_receives_intermediate_size() -> None:
    assert "self._prepare_final_geometry()" in MOTION
    assert "layout.activate()" in MOTION
    assert "self.content.resize(rect.size())" in MOTION
    assert "_smootherstep" in MOTION
    assert "self.viewport.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)" in MOTION
