from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
COMPOSER = (ROOT / "gui" / "workspace_composer.py").read_text(encoding="utf-8")
COMMIT = (ROOT / "gui" / "workspace_layout_commit.py").read_text(encoding="utf-8")


def test_composer_runs_once_after_every_widget_producer_and_before_presentation() -> None:
    install = RUNNER.index("install_workspace_composer(window)")
    for producer in (
        "install_ui_polish(window)",
        "details = install_card_details(window)",
        "install_mature_ui(window)",
        "window.install_mode_workspace()",
        "install_managed_makro_browser(window)",
        "install_required_input_support(window)",
        "install_listing_offer_support(window)",
        "install_single_ai_guidance(window)",
        "install_batch_product_files(window)",
        "install_listing_photo_ownership(window)",
        "install_batch_individual_controls(window.batch_workspace)",
        "install_cooperative_pause(window)",
        "install_activity_presence(window)",
        "install_workspace_layout_commit(window)",
    ):
        assert RUNNER.index(producer) < install, producer
    for consumer in (
        "visual.refresh_glass_frames()",
        "install_native_window_shell(window, quick_window)",
        "install_nekro_card_fx(window, visual)",
        "install_startup_entrance(window, visual)",
        "install_static_qml_view(window, visual, entrance_stability)",
        "shell.show()",
    ):
        assert install < RUNNER.index(consumer), consumer
    assert RUNNER.count("install_workspace_composer(window)") == 1


def test_former_competing_geometry_owners_are_gone() -> None:
    for name in ("page_scroll_layout", "console_summary_mode", "single_top_compact"):
        assert not (ROOT / "gui" / f"{name}.py").exists()
        assert name not in RUNNER
    assert "refresh_workspace_layout" in COMMIT
    assert "_console_summary_mode" not in COMMIT


def test_composer_moves_business_widgets_and_never_rebuilds_them() -> None:
    for constructor in (
        "ReadOnlyRunner(",
        "RealExecutionRunner(",
        "BatchController(",
        "QLineEdit(",
        "QCheckBox(",
        "QComboBox(",
        "QPlainTextEdit(",
    ):
        assert constructor not in COMPOSER
    # Real-execution controls stay alive for the settings modal proxies; Send to QC
    # stays the locked canonical checkbox and is only parked, never re-enabled.
    assert '"real_qc_check",' in COMPOSER
    assert "real_qc_check.setEnabled" not in COMPOSER
    assert "def _hidden_holder" in COMPOSER


def test_composer_owns_one_grid() -> None:
    assert "_PAGE_GAP = 12" in COMPOSER
    assert "_CARD_MARGINS = (20, 16, 20, 16)" in COMPOSER
    assert "_CONTROL_HEIGHT = 34" in COMPOSER
    assert "class SingleWorkspaceComposer" in COMPOSER
    assert "class BatchWorkspaceComposer" in COMPOSER
    assert "class HeaderComposer" in COMPOSER


def test_late_header_controls_are_reordered_without_layout_requests() -> None:
    header = COMPOSER.split("class HeaderComposer", 1)[1].split("# ----", 1)[0]
    assert "QEvent.Type.ChildAdded" in header
    assert "QEvent.Type.LayoutRequest" not in header
    assert "if order == self._order:" in header


@pytest.mark.skipif(sys.platform != "win32", reason="the real GUI shell targets Windows")
def test_real_gui_layout_keeps_every_business_control_reachable() -> None:
    pytest.importorskip("PySide6")
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gui_layout_probe.py"), "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    lines = [line for line in completed.stdout.splitlines() if line.startswith("GUI_LAYOUT_PROBE ")]
    assert lines, completed.stderr[-2000:]
    report = json.loads(lines[-1][len("GUI_LAYOUT_PROBE ") :])
    assert report["problems"] == []
    pages = {page["mode"]: page for page in report["pages"]}
    assert set(pages) == {"single", "batch"}
    assert len(pages["single"]["controls"]) >= 30
    assert len(pages["batch"]["controls"]) >= 14
    assert completed.returncode == 0
