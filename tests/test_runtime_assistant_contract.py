from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSISTANT = (ROOT / "gui" / "runtime_assistant.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "gui" / "runtime_assistant_toggle.py").read_text(encoding="utf-8")
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
    assert "WA_TranslucentBackground" not in ASSISTANT
    assert "WA_ShowWithoutActivating" in ASSISTANT
    assert "background-color: #0b1a2b" in ASSISTANT
    assert "border: none" in ASSISTANT
    assert "setWindowOpacity(0.96)" in ASSISTANT


def test_runtime_assistant_is_draggable_and_remembers_position() -> None:
    assert 'QSettings("ecommerce-agent", "RuntimeAssistant")' in ASSISTANT
    assert "def mousePressEvent" in ASSISTANT
    assert "def mouseMoveEvent" in ASSISTANT
    assert "def mouseReleaseEvent" in ASSISTANT
    assert "self.grabMouse()" in ASSISTANT
    assert "self.releaseMouse()" in ASSISTANT
    assert "WA_TransparentForMouseEvents" in ASSISTANT
    assert 'self._settings.setValue("position", self.pos())' in ASSISTANT
    assert 'self._settings.setValue("position_version", self._POSITION_VERSION)' in ASSISTANT
    assert "QApplication.screens()" in ASSISTANT
    assert "QApplication.screenAt" in ASSISTANT
    assert "self._COMPACT_WIDTH" in ASSISTANT
    assert "self._EXPANDED_WIDTH" in ASSISTANT


def test_runtime_assistant_defaults_to_top_right_and_migrates_old_position() -> None:
    assert "_POSITION_VERSION = 2" in ASSISTANT
    assert "version == self._POSITION_VERSION" in ASSISTANT
    assert "y = area.top() + self._SCREEN_MARGIN" in ASSISTANT
    assert "area.bottom() - self.height()" not in ASSISTANT


def test_runtime_assistant_float_window_is_default_off_but_monitoring_stays_installed() -> None:
    assert "self._user_visible = False" in ASSISTANT
    assert "def set_user_visible" in ASSISTANT
    assert "if self._user_visible:" in ASSISTANT
    assert "install_runtime_event_bridge(window)" in ASSISTANT
    assert "install_runtime_shadow_recovery(window)" in ASSISTANT
    assert "install_runtime_assistant_toggle(window, assistant)" in ASSISTANT
    assert "self.show()" not in ASSISTANT.split("QTimer.singleShot(0, self._restore_or_place)", 1)[0]


def test_hidden_runtime_assistant_defers_all_widget_mutation_until_opened() -> None:
    assert "def _apply_event_to_widgets" in ASSISTANT
    assert "def _set_label_text" in ASSISTANT
    present = ASSISTANT.split("    def present(self, event: RuntimeEvent) -> None:", 1)[1].split(
        "    def _compact_after_recovery", 1
    )[0]
    assert "self._last_event = event" in present
    assert "if not self._user_visible:" in present
    assert "self._settle_timer.stop()" in present
    assert "return" in present
    assert "self._apply_event_to_widgets(event)" in present

    visibility = ASSISTANT.split("    def set_user_visible", 1)[1].split(
        "    @staticmethod\n    def _is_button_child", 1
    )[0]
    assert "if self._last_event is not None:" in visibility
    assert "self._apply_event_to_widgets(self._last_event)" in visibility


def test_runtime_assistant_avoids_redundant_label_and_layout_churn_when_visible() -> None:
    assert "if label.text() != text:" in ASSISTANT
    assert "if self._expanded is expanded:" in ASSISTANT
    assert "return" in ASSISTANT.split("if self._expanded is expanded:", 1)[1].split(
        "self._expanded = expanded", 1
    )[0]


def test_runtime_assistant_switch_reuses_workspace_switch_and_defaults_off() -> None:
    assert "class RuntimeAssistantSwitch(WorkspaceModeSwitch)" in TOGGLE
    assert 'label = QLabel("浮窗", root)' in TOGGLE
    assert "toggle.set_checked_immediate(False)" in TOGGLE
    assert "setter(False)" in TOGGLE
    assert "toggle.toggled.connect(setter)" in TOGGLE
    assert "header.addWidget(toggle" in TOGGLE
    assert "QSettings" not in TOGGLE


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
