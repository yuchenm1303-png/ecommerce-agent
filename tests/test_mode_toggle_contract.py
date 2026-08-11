from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOGGLE = (ROOT / "gui" / "mode_toggle.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
WORKFLOW = (ROOT / "gui" / "workflow_console_window.py").read_text(encoding="utf-8")
TRANSITION = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")


def test_switch_matches_reference_el_switch_geometry() -> None:
    assert "_CORE_WIDTH = 40.0" in TOGGLE
    assert "_CORE_HEIGHT = 20.0" in TOGGLE
    assert "_ACTION_SIZE = 16.0" in TOGGLE
    assert "_ACTION_LEFT_OFF = 1.0" in TOGGLE
    assert "_ACTION_LEFT_ON = 23.0" in TOGGLE
    assert "self.setFixedSize(int(_CORE_WIDTH), 32)" in TOGGLE


def test_switch_matches_reference_300ms_cubic_bezier_and_supports_reversal() -> None:
    assert "_TRANSITION_MS = 300" in TOGGLE
    assert "QEasingCurve.Type.BezierSpline" in TOGGLE
    assert "QPointF(0.645, 0.045)" in TOGGLE
    assert "QPointF(0.355, 1.0)" in TOGGLE
    animate = TOGGLE.split("def _animate_to_state", 1)[1].split("def _sync_tooltip", 1)[0]
    assert "self._animation.stop()" in animate
    assert "self._animation.setStartValue(self._action_position)" in animate
    assert "self._animation.setEndValue(1.0 if checked else 0.0)" in animate


def test_switch_matches_reference_settings_panel_visual_override_and_inline_prompts() -> None:
    assert "QColor(255, 255, 255, 0x30)" in TOGGLE
    assert "painter.drawRoundedRect(core, 10.0, 10.0)" in TOGGLE
    assert 'painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, "✓")' in TOGGLE
    assert 'painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, "×")' in TOGGLE
    assert "painter.drawEllipse(action)" in TOGGLE
    assert "hover" not in TOGGLE.lower()


def test_switch_replaces_only_the_legacy_mode_row_presentation() -> None:
    assert "legacy_card.setObjectName(\"\")" in TOGGLE
    assert "legacy_card.hide()" in TOGGLE
    assert "header.addWidget(toggle, 0, Qt.AlignmentFlag.AlignBottom)" in TOGGLE
    assert 'transition = getattr(window, "_workspace_transition_controller", None)' in TOGGLE
    assert 'request = getattr(transition, "request_mode", None)' in TOGGLE
    assert "toggle.clicked.connect(request_mode)" in TOGGLE
    assert "mode_stack.currentChanged.connect(sync_from_stack)" in TOGGLE
    assert "install_workspace_mode_switch(window)" in RUNNER
    assert "install_workspace_transition(window, visual)" in RUNNER

    # The established business state machine remains the source of truth.
    assert 'self.single_mode_button = QPushButton("SINGLE")' in WORKFLOW
    assert 'self.batch_mode_button = QPushButton("BATCH")' in WORKFLOW
    assert "def _set_workspace_mode" in WORKFLOW
    assert 'self._set_mode = getattr(window, "_set_workspace_mode", None)' in TRANSITION


def test_switch_does_not_create_another_workspace_or_business_controller() -> None:
    assert "QStackedWidget" not in TOGGLE
    assert "BatchWorkspace" not in TOGGLE
    assert "QTimer" not in TOGGLE


def test_mode_toggle_sources_compile_without_importing_pyside() -> None:
    compile(TOGGLE, str(ROOT / "gui" / "mode_toggle.py"), "exec")
    compile(TRANSITION, str(ROOT / "gui" / "workspace_transition.py"), "exec")
    compile(RUNNER, str(ROOT / "run_local_gui.py"), "exec")
