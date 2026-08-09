from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIAG = (ROOT / "gui" / "window_diagnostics.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_diagnostics_are_explicitly_opt_in() -> None:
    assert 'os.environ.get("ECOM_GUI_DIAGNOSTICS", "").strip() != "1"' in DIAG
    assert "install_window_diagnostics(window, visual.background, shell, project_root)" in RUNNER


def test_diagnostics_capture_both_qt_and_win32_geometry() -> None:
    assert "owner_qt" in DIAG
    assert "overlay_qt" in DIAG
    assert "owner_win32" in DIAG
    assert "overlay_win32" in DIAG
    assert "overlay_minus_owner_client" in DIAG
    assert "quick_relative_qt" in DIAG
    assert "quick_relative_win32" in DIAG
    assert "GetClientRect" in DIAG
    assert "ClientToScreen" in DIAG
    assert "GetWindowRect" in DIAG


def test_diagnostics_measure_real_quick_present_cadence_without_qml_changes() -> None:
    assert "self.quick.frameSwapped.connect(self._on_frame_swapped)" in DIAG
    assert "quick_frame_hz" in DIAG
    assert "frame_interval_p50_ms" in DIAG
    assert "frame_interval_p95_ms" in DIAG
    assert "frame_interval_p99_ms" in DIAG
    assert "ui_timer_lag_ms" in DIAG
    assert "mouse_hz" in DIAG
    assert "diagnosticsEnabled" not in DIAG


def test_diagnostics_have_fixed_latest_log_path() -> None:
    assert '"window-diagnostics-latest.jsonl"' in DIAG
