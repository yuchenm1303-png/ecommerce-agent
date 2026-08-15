from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOURNAL = (ROOT / "gui" / "async_run_journal.py").read_text(encoding="utf-8")
READONLY = (ROOT / "gui" / "readonly_runner.py").read_text(encoding="utf-8")
REAL = (ROOT / "gui" / "real_execution.py").read_text(encoding="utf-8")
BATCH = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")


def test_runtime_sources_compile_without_running_qt() -> None:
    for relative, source in (
        ("gui/async_run_journal.py", JOURNAL),
        ("gui/readonly_runner.py", READONLY),
        ("gui/real_execution.py", REAL),
        ("gui/batch_runner.py", BATCH),
    ):
        compile(source, relative, "exec")


def test_single_workflow_and_real_execution_do_not_write_each_stdout_line_on_gui_thread() -> None:
    assert "from .async_run_journal import AsyncRunJournal" in READONLY
    assert "from .async_run_journal import AsyncRunJournal" in REAL
    assert "journal.append(line)" in READONLY
    assert "journal.append(line)" in REAL
    assert 'with (self.run_dir / "gui-workflow.log").open' not in READONLY
    assert 'with (self.output_root / "real-execution-gui.log").open' not in REAL
    assert "Thread(" in JOURNAL
    assert "self._queue.put(str(line))" in JOURNAL
    assert "with self.path.open(" in JOURNAL


def test_real_execution_live_log_has_a_fixed_ui_repaint_budget() -> None:
    for token in (
        "_LIVE_LOG_FLUSH_MS = 120",
        "_LIVE_LOG_BATCH_LINES = 80",
        "_LIVE_LOG_PENDING_LINES = 1600",
        "_LIVE_LOG_VISIBLE_BLOCKS = 1800",
        "deque(maxlen=_LIVE_LOG_PENDING_LINES)",
        "count = len(self._pending_logs) if drain else min(",
        "self.log_view.setUpdatesEnabled(False)",
        "self.log_view.setUpdatesEnabled(True)",
    ):
        assert token in REAL
    assert "setMaximumBlockCount(12000)" not in REAL


def test_batch_telemetry_and_state_publication_are_coalesced_for_high_worker_counts() -> None:
    for token in (
        "_BATCH_LOG_PREVIEW_MS = 140",
        "_BATCH_STATE_PUBLISH_MS = 180",
        "self._pending_log_preview: dict[str, str] = {}",
        "self._queue_log_preview(job_id",
        "self._state_dirty = True",
        "self._state_publish_timer.start()",
        "def _flush_persist_emit(self)",
    ):
        assert token in BATCH
    observe = BATCH.split("def _observe_line", 1)[1].split("def _finished", 1)[0]
    assert "self.log.emit(" not in observe
    assert "save_batch_run(self.batch)" not in observe


def test_performance_work_does_not_relax_execution_safety() -> None:
    assert '"send_to_qc": False' in REAL
    assert 'send_to_qc=False (repository policy lock)' in REAL
    assert '"--send-to-qc"' not in REAL
    assert "self.batch.send_to_qc = False" in BATCH
