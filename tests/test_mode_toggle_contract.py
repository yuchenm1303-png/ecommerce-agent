from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOGGLE = (ROOT / "gui" / "mode_toggle.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
WORKFLOW = (ROOT / "gui" / "workflow_console_window.py").read_text(encoding="utf-8")


def test_compact_toggle_replaces_visible_full_width_mode_row_only() -> None:
    assert "class WorkspaceModeToggle(QAbstractButton)" in TOGGLE
    assert "legacy_card.setObjectName(\"\")" in TOGGLE
    assert "legacy_card.hide()" in TOGGLE
    assert "header.addWidget(toggle, 0, Qt.AlignmentFlag.AlignBottom)" in TOGGLE
    assert "install_compact_mode_toggle(window)" in RUNNER

    # Keep the original business workspace controls/state machine intact behind
    # the compact presentation adapter instead of reimplementing Batch logic.
    assert 'self.single_mode_button = QPushButton("SINGLE")' in WORKFLOW
    assert 'self.batch_mode_button = QPushButton("BATCH")' in WORKFLOW
    assert "def _set_workspace_mode" in WORKFLOW


def test_toggle_uses_one_lightweight_thumb_animation() -> None:
    assert "self.setFixedSize(106, 34)" in TOGGLE
    assert "self._animation = QPropertyAnimation(self, b\"thumbPosition\", self)" in TOGGLE
    assert "self._animation.setDuration(self._ANIMATION_MS)" in TOGGLE
    assert "QEasingCurve.Type.OutCubic" in TOGGLE
    assert "painter.drawRoundedRect" in TOGGLE
    assert "painter.drawEllipse" in TOGGLE
    assert 'label = "SINGLE"' in TOGGLE
    assert 'label = "BATCH"' in TOGGLE


def test_toggle_does_not_create_another_workspace_or_business_controller() -> None:
    assert "QStackedWidget" not in TOGGLE
    assert "BatchWorkspace" not in TOGGLE
    assert "set_mode(1 if checked else 0)" in TOGGLE
    assert "mode_stack.currentChanged.connect(sync_from_stack)" in TOGGLE


def test_mode_toggle_sources_compile_without_importing_pyside() -> None:
    compile(TOGGLE, str(ROOT / "gui" / "mode_toggle.py"), "exec")
    compile(RUNNER, str(ROOT / "run_local_gui.py"), "exec")
