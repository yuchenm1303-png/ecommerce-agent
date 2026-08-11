from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSISTANT = (ROOT / "gui" / "runtime_assistant.py").read_text(encoding="utf-8")
BRIDGE = (ROOT / "gui" / "runtime_event_bridge.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_runtime_assistant_is_non_modal_and_lightweight() -> None:
    assert "QGraphicsBlurEffect" not in ASSISTANT
    assert "QGraphicsOpacityEffect" not in ASSISTANT
    assert "QMessageBox" not in ASSISTANT
    assert "setWindowModality" not in ASSISTANT
    assert "WA_StyledBackground" in ASSISTANT
    assert "Runtime Assistant requires a central widget" in ASSISTANT


def test_phase_one_is_explicit_shadow_mode() -> None:
    assert "SHADOW_MODE = True" in BRIDGE
    assert "Shadow Mode" in BRIDGE
    assert "RecoveryAgent(" not in BRIDGE
    assert "click(" not in BRIDGE
    assert "page." not in BRIDGE


def test_formal_launcher_installs_runtime_assistant_after_other_overlay_surfaces() -> None:
    assert "from gui.runtime_assistant import install_runtime_assistant" in LAUNCHER
    assert "install_activity_presence(window)" in LAUNCHER
    assert "install_detailed_preparation_progress(window)" in LAUNCHER
    assert "assistant = install_runtime_assistant(window)" in LAUNCHER
    assert LAUNCHER.index("effects.raise_()") < LAUNCHER.index(
        "assistant = install_runtime_assistant(window)"
    )
