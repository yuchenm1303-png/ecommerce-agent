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
    assert "_FRAME_MS = 6" in MOTION
    assert "self.content.resize(rect.size())" in MOTION
    assert "self.viewport.setGeometry" in MOTION
    assert "self.content.move" in MOTION
    assert "QPropertyAnimation" not in MOTION
    assert "QParallelAnimationGroup" not in MOTION
    assert "QGraphicsOpacityEffect" not in MOTION
    assert "setMinimumHeight" not in MOTION
    assert "setMaximumHeight" not in MOTION
    assert "setSizes(" not in MOTION


def test_focus_mode_reveals_fixed_final_content_from_source_region() -> None:
    assert '"focus"' in MOTION
    assert "origin_provider" in MOTION
    assert "def _prepare_focus_origin" in MOTION
    assert "def _apply_focus" in MOTION
    assert "self.content.move(final.x() - rect.x(), final.y() - rect.y())" in MOTION
    assert "self.content.resize(rect.size())" in MOTION


def test_real_settings_stay_in_anchored_sheet_without_main_layout_motion() -> None:
    assert "def _build_real_sheet" in SHEETS
    assert 'widget.setParent(self.real_sheet)' in SHEETS
    assert 'edge="top"' in SHEETS
    assert "setMinimumHeight" not in SHEETS.split("def _build_real_sheet", 1)[1].split(
        "def _prepare_console_summary", 1
    )[0]
    assert "setSizes(" not in SHEETS.split("def _build_real_sheet", 1)[1].split(
        "def _prepare_console_summary", 1
    )[0]


def test_console_old_expanded_state_is_now_permanent_summary_state() -> None:
    assert "def _prepare_console_summary" in SHEETS
    assert "window._console_summary_mode = True" in SHEETS
    assert "toggle.toggled.disconnect()" in SHEETS
    assert "toggle.setCheckable(True)" in SHEETS
    assert "toggle.setChecked(True)" in SHEETS
    assert 'toggle.setText("展开详情 ⌄")' in SHEETS
    assert "unit.show()" in SHEETS
    assert "tabs.show()" in SHEETS
    assert "console.setMinimumHeight(300)" in SHEETS
    assert "console.setMaximumHeight(460)" in SHEETS


def test_console_is_not_reparented_into_anchored_sheet_anymore() -> None:
    assert "def _build_console_sheet" not in SHEETS
    assert "console_sheet" not in SHEETS
    assert 'unit.setParent(self.console_sheet)' not in SHEETS
    assert 'tabs.setParent(self.console_sheet)' not in SHEETS


def test_console_expand_button_no_longer_drives_layout_visibility() -> None:
    assert "def _restore_console_summary_toggle" in SHEETS
    assert "toggle.setChecked(True)" in SHEETS
    assert "toggle.setText(\"展开详情 ⌄\")" in SHEETS


def test_sheet_motion_keeps_text_at_final_size_for_entire_transition() -> None:
    assert "self._prepare_final_geometry()" in MOTION
    assert "layout.activate()" in MOTION
    assert "self.content.resize(rect.size())" in MOTION
    assert "_smootherstep" in MOTION
    assert "self.viewport.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)" in MOTION
