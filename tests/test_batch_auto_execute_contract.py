from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTO = (ROOT / "gui" / "batch_auto_execute.py").read_text(encoding="utf-8")
BOOTSTRAP = (ROOT / "gui" / "__init__.py").read_text(encoding="utf-8")


def test_auto_execute_toggle_is_explicit_and_off_by_default() -> None:
    assert 'QCheckBox("自动正式填写"' in AUTO
    assert 'setObjectName("batchAutoExecuteCheck")' in AUTO
    assert "toggle.setChecked(False)" in AUTO
    assert "self.controller.jobs_changed.connect(self._on_jobs_changed)" in AUTO


def test_ready_jobs_reuse_existing_owned_context_and_execution_queue() -> None:
    assert 'str(getattr(job, "status", "")) != "READY"' in AUTO
    assert "self.individual._job_is_scheduled(job_id)" in AUTO
    assert "self.individual._bind_job_context(row, job)" in AUTO
    assert "self.controller._execute_queue.append(job_id)" in AUTO
    assert "self.individual._pump_lanes()" in AUTO


def test_auto_execute_matches_manual_real_fill_authorization_without_confirmation() -> None:
    assert "batch.save_authorized = True" in AUTO
    assert "batch.images_authorized = True" in AUTO
    assert "batch.send_to_qc = False" in AUTO
    assert "self.controller._execution_images = True" in AUTO
    assert "QMessageBox" not in AUTO


def test_stop_state_blocks_new_automatic_execution() -> None:
    assert 'getattr(self.controller, "_stopping", False)' in AUTO
    assert 'str(getattr(batch, "status", "")) == "STOPPED"' in AUTO


def test_deferred_install_keeps_startup_isolated_and_refreshes_quick_topology() -> None:
    assert "def install_batch_auto_execute_extension" in BOOTSTRAP
    assert "from .batch_auto_execute import install_batch_auto_execute" in BOOTSTRAP
    assert "QTimer.singleShot(0, install_batch_auto_execute_extension)" in BOOTSTRAP
    assert 'getattr(bridge, "schedule_structure_refresh", None)' in BOOTSTRAP
