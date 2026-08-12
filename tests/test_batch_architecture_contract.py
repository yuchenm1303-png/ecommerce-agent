from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OWNER = (ROOT / "app" / "browser_page_owner.py").read_text(encoding="utf-8")
SOURCE = (ROOT / "makro_batch_source.py").read_text(encoding="utf-8")
JOB = (ROOT / "makro_batch_job.py").read_text(encoding="utf-8")
EXECUTOR = (ROOT / "makro_execute_listing.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")
WORKSPACE = (ROOT / "gui" / "batch_workspace.py").read_text(encoding="utf-8")
WINDOW = (ROOT / "gui" / "workflow_console_window.py").read_text(encoding="utf-8")


def test_batch_jobs_own_exact_makro_tabs_instead_of_guessing_current_page() -> None:
    assert "Target.getTargetInfo" in OWNER
    assert "context.new_page()" in JOB
    assert "page_target_id(page)" in JOB
    assert '"makro_target_id"' in JOB
    assert "find_page_by_target_id" in EXECUTOR
    assert '"--makro-target-id"' in EXECUTOR
    assert 'if not args.makro_target_id:\n            _assert_single_listing_tab' in EXECUTOR


def test_batch_source_navigation_is_prefetched_before_parallel_prepare() -> None:
    assert "capture_product_source" in SOURCE
    assert "self._source_queue" in RUNNER
    assert 'source_active = any(stage == "source"' in RUNNER
    assert "Batch source cache miss" in JOB
    assert "captured.cache_hit" in JOB


def test_batch_reuses_canonical_business_pipeline_and_executor() -> None:
    assert "infer_listing_bootstrap" in JOB
    assert "select_vertical(page, provider, hints)" in JOB
    assert "select_brand(page, provider, hints)" in JOB
    assert "_prepare_step3(args, run_dir=run_dir, page=page, manifest=manifest)" in JOB
    assert '"makro_execute_listing.py"' in RUNNER
    assert '"--all-step3"' in RUNNER
    assert '"--allow-section-save"' in RUNNER
    assert '"--upload-image"' in RUNNER


def test_batch_and_single_are_separate_full_workspaces_in_one_window() -> None:
    assert "QStackedWidget" in WINDOW
    assert 'QPushButton("SINGLE")' in WINDOW
    assert 'QPushButton("BATCH")' in WINDOW
    assert "self.mode_stack.addWidget(single_page)" in WINDOW
    assert "BatchWorkspace(" in WINDOW
    assert "QPlainTextEdit" in WORKSPACE
    assert "class BatchJobCard(QFrame)" in WORKSPACE
    assert "QScrollArea" in WORKSPACE
    assert "QProgressBar" in WORKSPACE
    assert "self._job_cards" in WORKSPACE
    assert "批量准备" in WORKSPACE
    assert "批量填写 READY" in WORKSPACE


def test_batch_job_surface_keeps_independent_live_logs_and_owned_tab_metadata() -> None:
    assert "_JOB_LOG_LINE" in WORKSPACE
    assert "self.controller.log.connect(self._append_controller_log)" in WORKSPACE
    assert "def append_log(self, line: str)" in WORKSPACE
    assert "setMaximumBlockCount(_MAX_JOB_LOG_LINES)" in WORKSPACE
    assert "Makro targetId" in WORKSPACE
    assert "Execution report" in WORKSPACE
    assert "READY  {job.ready}" in WORKSPACE
    assert "BLOCKED  {job.blocked}" in WORKSPACE
    assert "owned tab 状态、实时进度和独立日志" in WORKSPACE


def test_batch_never_enables_send_to_qc() -> None:
    combined = JOB + EXECUTOR + RUNNER + WORKSPACE
    assert 'send_to_qc_clicked": False' in JOB
    assert '"send_to_qc_clicked": False' in EXECUTOR
    assert "self.batch.send_to_qc = False" in RUNNER
    assert "Send to QC · LOCKED" in WORKSPACE
    assert '"--send-to-qc"' not in RUNNER
