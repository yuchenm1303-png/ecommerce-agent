from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "batch_job_controls.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")


def test_each_batch_job_card_gets_individual_run_and_pause_controls() -> None:
    assert 'run_button = QPushButton("单独真实填写")' in SOURCE
    assert 'pause_button = QPushButton("暂停")' in SOURCE
    assert "self.start_job_execution(jid)" in SOURCE
    assert "self.toggle_pause(jid)" in SOURCE
    assert 'controls.pause_button.setText("继续" if paused' in SOURCE


def test_single_job_execution_reuses_canonical_batch_executor_queue() -> None:
    assert 'if job.status != "READY":' in SOURCE
    assert 'self.controller._execute_queue.append(job_id)' in SOURCE
    assert "self.controller._pump_execute()" in SOURCE
    assert "self.controller._execution_images = True" in SOURCE
    assert "batch.save_authorized = True" in SOURCE
    assert "batch.images_authorized = True" in SOURCE
    assert "batch.send_to_qc = False" in SOURCE
    assert '"makro_execute_listing.py"' in RUNNER
    assert '"--makro-target-id"' in RUNNER


def test_pause_is_scheduler_safe_and_never_force_kills_active_makro_process() -> None:
    assert "self._pause_requested[job_id] = active_stage" in SOURCE
    assert "当前阶段完成后暂停" in SOURCE
    assert "self._remove_job(self.controller._prepare_queue, job_id)" in SOURCE
    assert "self._remove_job(self.controller._execute_queue, job_id)" in SOURCE
    assert "process.terminate" not in SOURCE
    assert "QProcess" not in SOURCE
    assert "persistence transaction" in SOURCE


def test_paused_ready_job_is_excluded_until_resume() -> None:
    assert '_PAUSED_STATUS = "PAUSED"' in SOURCE
    assert 'job.status = _PAUSED_STATUS' in SOURCE
    assert 'job.status = "READY"' in SOURCE
    assert "已暂停 · 不参与批量调度" in SOURCE


def test_formal_gui_installs_batch_job_controls_after_batch_workspace_exists() -> None:
    assert "from gui.batch_job_controls import install_batch_job_controls" in RUN
    assert "window.install_mode_workspace()" in RUN
    assert "install_batch_job_controls(window.batch_workspace)" in RUN
    assert RUN.index("window.install_mode_workspace()") < RUN.index(
        "install_batch_job_controls(window.batch_workspace)"
    )


def test_batch_job_controls_source_compiles_without_importing_pyside() -> None:
    compile(SOURCE, str(ROOT / "gui" / "batch_job_controls.py"), "exec")
