from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TUNING = (ROOT / "gui" / "workspace_transition_tuning.py").read_text(encoding="utf-8")
TRANSITION = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_slower_profile_changes_timing_only() -> None:
    assert '"_HOLD_MS": 50' in TUNING
    assert '"_EXIT_END_MS": 190' in TUNING
    assert '"_ENTER_START_MS": 215' in TUNING
    assert '"_TOTAL_MS": 480' in TUNING
    assert '"_ENTER_DURATION_MS": 265' in TUNING
    assert '"_HEADER_EXIT_START_MS": 55' in TUNING
    assert '"_HEADER_EXIT_END_MS": 155' in TUNING
    assert '"_HEADER_ENTER_START_MS": 185' in TUNING
    assert '"_HEADER_ENTER_END_MS": 330' in TUNING
    assert '"_VEIL_START_MS": 165' in TUNING
    assert '"_VEIL_PEAK_MS": 210' in TUNING
    assert '"_VEIL_END_MS": 270' in TUNING

    # The stabilized renderer stays byte-level independent of speed tuning.
    assert "_HOLD_MS = 40" in TRANSITION
    assert "_EXIT_END_MS = 155" in TRANSITION
    assert "_ENTER_START_MS = 175" in TRANSITION
    assert "_TOTAL_MS = 390" in TRANSITION
    assert "page.render(" in TRANSITION
    assert "page.grab()" not in TRANSITION


def test_formal_runner_applies_profile_before_installing_transition() -> None:
    assert "from gui.workspace_transition_tuning import apply_workspace_transition_tuning" in RUNNER
    assert "apply_workspace_transition_tuning()" in RUNNER
    assert RUNNER.index("apply_workspace_transition_tuning()") < RUNNER.index(
        "install_workspace_transition(window, visual)"
    )


def test_tuning_source_compiles_without_importing_pyside() -> None:
    compile(TUNING, str(ROOT / "gui" / "workspace_transition_tuning.py"), "exec")
