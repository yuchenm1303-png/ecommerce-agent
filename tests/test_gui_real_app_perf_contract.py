from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "gui_real_app_perf.py"
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")


def test_real_app_profiler_is_development_only_and_compiles() -> None:
    source = TOOL.read_text(encoding="utf-8")
    compile(source, str(TOOL), "exec")
    assert "gui_real_app_perf" not in RUN
    assert "legacy-toggle" not in RUN
    assert "real_gui_perf" not in VISUAL


def test_real_app_profiler_measures_real_runtime_surfaces() -> None:
    source = TOOL.read_text(encoding="utf-8")
    for token in (
        'VARIANTS = ("legacy-toggle", "current")',
        "quick.frameSwapped.connect(self._on_swap)",
        "timer.timeout.connect(self._on_presentation_tick)",
        "QEvent.Type.UpdateRequest",
        "QEvent.Type.Paint",
        "QCursor.setPos(self._points[index])",
        "_TimedLegacyToggleEffect",
        "_TimedCurrentEffect",
        "REAL GUI PERF SUMMARY",
        "REAL GUI A/B · current vs legacy-toggle",
    ):
        assert token in source


def test_real_app_ab_changes_only_effect_lifecycle_inside_tool_process() -> None:
    source = TOOL.read_text(encoding="utf-8")
    assert "visual_module._CardScaleEffect = (" in source
    assert "self.setEnabled(False)" in source
    assert "self.setEnabled(active)" in source
    assert "self.updateBoundingRect()" in source
    assert "import run_local_gui" in source
    assert "run_local_gui.main()" in source
    assert "setEnabled(active)" not in VISUAL
    assert "updateBoundingRect()" not in VISUAL


def test_real_app_profiler_owns_foreground_and_clean_shutdown() -> None:
    source = TOOL.read_text(encoding="utf-8")
    for token in (
        "ctypes.windll.user32",
        "SetWindowPos",
        "SetForegroundWindow",
        "self._set_foreground(topmost=True)",
        "self._set_foreground(topmost=False)",
        "app.closeAllWindows()",
        "app.exit(0)",
    ):
        assert token in source
