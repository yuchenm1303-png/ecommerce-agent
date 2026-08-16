from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLS = (ROOT / "gui" / "batch_individual_controls.py").read_text(encoding="utf-8")
ENTRY = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_batch_rows_have_independent_start_stop_delete_without_pause() -> None:
    assert 'QPushButton("启动"' in CONTROLS
    assert 'QPushButton("停止"' in CONTROLS
    assert 'remove.setText("删除")' in CONTROLS
    assert 'QPushButton("暂停"' not in CONTROLS
    assert "start_row" in CONTROLS
    assert "stop_job" in CONTROLS
    assert "delete_job" in CONTROLS


def test_one_job_stop_does_not_use_global_batch_stop() -> None:
    assert "self._stop_requested" in CONTROLS
    assert "process.terminate()" in CONTROLS
    assert "self.controller.stop()" not in CONTROLS
    assert 'job.status = "STOPPED"' in CONTROLS
    assert "self._remove_from_queues(job_id)" in CONTROLS


def test_individual_start_keeps_job_owned_intent_files_and_photos() -> None:
    assert "_listing_offer_intent_by_job_id" in CONTROLS
    assert "_supplemental_product_files_by_job_id" in CONTROLS
    assert "_write_intent_sidecar" in CONTROLS
    assert 'getattr(self.window, "_listing_photo_ownership"' in CONTROLS
    assert "set_images(job, files)" in CONTROLS


def test_formal_gui_installs_individual_batch_controls_not_batch_pause_controls() -> None:
    assert "from gui.batch_individual_controls import install_batch_individual_controls" in ENTRY
    assert "install_batch_individual_controls(window.batch_workspace)" in ENTRY
    assert "install_cooperative_batch_job_controls(window.batch_workspace)" not in ENTRY
    assert "from gui.cooperative_pause import install_cooperative_pause" in ENTRY
