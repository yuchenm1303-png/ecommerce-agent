from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSISTANT = (ROOT / "gui" / "runtime_assistant.py").read_text(encoding="utf-8")
BRIDGE = (ROOT / "gui" / "runtime_event_bridge.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_runtime_assistant_is_independent_non_modal_tool_window() -> None:
    assert "QGraphicsBlurEffect" not in ASSISTANT
    assert "QGraphicsOpacityEffect" not in ASSISTANT
    assert "QMessageBox" not in ASSISTANT
    assert "setWindowModality" not in ASSISTANT
    assert "window.centralWidget()" not in ASSISTANT
    assert "super().__init__(None, flags)" in ASSISTANT
    assert "Qt.WindowType.Tool" in ASSISTANT
    assert "Qt.WindowType.FramelessWindowHint" in ASSISTANT
    assert "Qt.WindowType.WindowStaysOnTopHint" in ASSISTANT
    assert "WA_TranslucentBackground" in ASSISTANT
    assert "WA_ShowWithoutActivating" in ASSISTANT


def test_runtime_assistant_is_draggable_and_remembers_position() -> None:
    assert "QSettings(\"ecommerce-agent\", \"RuntimeAssistant\")" in ASSISTANT
    assert "def mousePressEvent" in ASSISTANT
    assert "def mouseMoveEvent" in ASSISTANT
    assert "def mouseReleaseEvent" in ASSISTANT
    assert "self._settings.setValue(\"position\", self.pos())" in ASSISTANT
    assert "QApplication.screens()" in ASSISTANT
    assert "QApplication.screenAt" in ASSISTANT
    assert "self._COMPACT_WIDTH" in ASSISTANT
    assert "self._EXPANDED_WIDTH" in ASSISTANT


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
