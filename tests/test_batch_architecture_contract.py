from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OWNER = (ROOT / "app" / "browser_page_owner.py").read_text(encoding="utf-8")
SOURCE = (ROOT / "makro_batch_source.py").read_text(encoding="utf-8")
JOB = (ROOT / "makro_batch_job.py").read_text(encoding="utf-8")
STEP3 = (ROOT / "app" / "batch_step3_prepare.py").read_text(encoding="utf-8")
TRANSPORT = (ROOT / "app" / "cdp_transport_lane.py").read_text(encoding="utf-8")
PARALLEL = (ROOT / "gui" / "batch_parallel_runtime.py").read_text(encoding="utf-8")
WORKFLOW = (ROOT / "makro_gui_workflow.py").read_text(encoding="utf-8")
EXECUTOR = (ROOT / "makro_execute_listing.py").read_text(encoding="utf-8")
OWNED_EXECUTOR = (ROOT / "makro_execute_owned.py").read_text(encoding="utf-8")
MODEL = (ROOT / "gui" / "batch_model.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")
WORKSPACE = (ROOT / "gui" / "batch_workspace.py").read_text(encoding="utf-8")
CONTROLS = (ROOT / "gui" / "batch_job_controls.py").read_text(encoding="utf-8")
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
    assert "acquired.source_cache_hit" in JOB


def test_batch_reuses_canonical_business_pipeline_and_executor() -> None:
    assert "infer_listing_bootstrap" in JOB
    assert "_advance_listing_to_step3" in JOB
    assert "select_vertical(page, provider, hints)" in WORKFLOW
    assert "select_brand_to_product_info(page, provider, hints)" in WORKFLOW
    assert "capture_batch_step3_schema(" in JOB
    assert "complete_batch_step3_from_schema(" in JOB
    assert "_run_resolver_pair" in STEP3
    assert "_resolver_pair_for_pack" in STEP3
    assert '"makro_execute_listing.py"' in RUNNER
    assert "from makro_execute_listing import main as execute_main" in OWNED_EXECUTOR
    assert '"--all-step3"' in RUNNER
    assert '"--allow-section-save"' in RUNNER
    assert '"--upload-image"' in RUNNER


def test_batch_browser_transport_is_single_while_ai_remains_parallel() -> None:
    assert "exclusive_cdp_transport_lane(args.cdp_port)" in JOB
    assert "transport_released_before_resolver=True" in JOB
    assert JOB.index("harness.detach()") < JOB.index("complete_batch_step3_from_schema(")
    assert "transport-lane-" in TRANSPORT
    assert "_try_lock_handle" in TRANSPORT
    assert "exclusive_cdp_transport_lane(port)" in OWNED_EXECUTOR
    assert "poison_matches_current_generation(port)" in OWNED_EXECUTOR
    assert 'routed[0] = "makro_execute_owned.py"' in PARALLEL
    assert "mode=in-process-owned-worker" in PARALLEL


def test_transport_lane_is_ownership_only_and_never_launches_a_child_runtime() -> None:
    assert "subprocess" not in TRANSPORT
    assert "sys.executable" not in TRANSPORT
    assert "python_args_under_transport_lane" not in TRANSPORT
    assert "app.cdp_transport_lane" not in PARALLEL


def test_batch_top_level_rejects_cross_workspace_overlap_before_session_lease() -> None:
    start = PARALLEL.split("        def start_prepare(", 1)[1].split(
        "        def start_execution(", 1
    )[0]
    assert "runtime._assert_top_level_idle()" in start
    assert start.index("runtime._assert_top_level_idle()") < start.index("runtime.manager.ensure_ready(")
    assert start.index("runtime._assert_top_level_idle()") < start.index("runtime._ensure_owner(")


def test_ready_job_can_execute_while_other_batch_jobs_keep_preparing() -> None:
    assert "def _pump_prepare_lane(" in CONTROLS
    assert "def _pump_execute_lane(" in CONTROLS
    assert "self._pump_prepare_lane()" in CONTROLS
    assert "self._pump_execute_lane()" in CONTROLS
    assert "self.controller._start_execute_job" in CONTROLS
    assert 'self.controller._mode in {"idle", "execute"}' not in CONTROLS
    assert "READY · 可单独填写 · 其他任务继续" in CONTROLS


def test_batch_worker_limit_is_shared_and_supports_sixteen() -> None:
    assert "BATCH_WORKER_MIN = 1" in MODEL
    assert "BATCH_WORKER_DEFAULT = 6" in MODEL
    assert "BATCH_WORKER_MAX = 16" in MODEL
    assert "normalize_batch_concurrency(execute_concurrency)" in RUNNER
    assert "self.worker_count.setRange(BATCH_WORKER_MIN, BATCH_WORKER_MAX)" in WORKSPACE
    assert "self.worker_count.setValue(BATCH_WORKER_DEFAULT)" in WORKSPACE
    assert "self.worker_count.setRange(1, 3)" not in WORKSPACE
    assert "min(4, int(execute_concurrency))" not in RUNNER


def test_batch_and_single_share_page_progress_reconciliation() -> None:
    assert "def _advance_listing_to_step3(" in WORKFLOW
    assert "state-machine reconcile" in WORKFLOW
    assert "allow_initial_later_stage" in WORKFLOW
    assert "_advance_listing_to_step3(" in JOB
    assert "_listing_stage(page)" in JOB


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


def test_batch_job_surface_keeps_independent_lossless_logs_and_owned_tab_metadata() -> None:
    assert "_JOB_LOG_LINE" in WORKSPACE
    assert "self.controller.log.connect(self._append_controller_log)" in WORKSPACE
    assert "def append_log(self, line: str)" in WORKSPACE
    assert "self._logs: deque[str] = deque()" in WORKSPACE
    assert "setMaximumBlockCount(" not in WORKSPACE
    assert "Makro targetId" in WORKSPACE
    assert "Execution report" in WORKSPACE
    assert "READY  {job.ready}" in WORKSPACE
    assert "BLOCKED  {job.blocked}" in WORKSPACE


def test_batch_never_enables_send_to_qc() -> None:
    combined = JOB + EXECUTOR + RUNNER + WORKSPACE
    assert 'send_to_qc_clicked": False' in JOB
    assert '"send_to_qc_clicked": False' in EXECUTOR
    assert "self.batch.send_to_qc = False" in RUNNER
    assert "Send to QC · LOCKED" in WORKSPACE
    assert '"--send-to-qc"' not in RUNNER
