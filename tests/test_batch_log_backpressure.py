from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow

from gui.batch_log_buffer import (
    BATCH_LOG_FLUSH_LINES,
    BATCH_LOG_MAX_LINE_CHARS,
    BATCH_LOG_MAX_LINES,
    BATCH_LOG_PENDING_LINES,
    display_log_line,
    log_buffer,
)
from gui.quick_batch_list import QuickBatchList
from gui.batch_runner import BatchController


def _job() -> SimpleNamespace:
    return SimpleNamespace(
        job_id="JOB-001",
        status="RESOLVING",
        progress=70,
        product_name="test",
        product_url="https://example.invalid/item",
        vertical="test",
        brand="test",
        ready=0,
        blocked=0,
        required_blocked=0,
        image_count=0,
        stage_detail="resolving",
        error="",
        run_dir="",
        makro_target_id="",
        execution_report="",
        updated_at="",
    )


def test_display_log_buffer_is_bounded() -> None:
    lines = [f"line-{index}-" + ("x" * 4_000) for index in range(BATCH_LOG_MAX_LINES + 25)]
    buffered = log_buffer(lines)

    assert len(buffered) == BATCH_LOG_MAX_LINES
    assert buffered[0].startswith("line-25-")
    assert all(len(line) <= BATCH_LOG_MAX_LINE_CHARS + 40 for line in buffered)
    assert "UI truncated" in display_log_line("x" * 4_000)


def test_quick_batch_logs_are_bounded_and_full_text_refresh_is_coalesced() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    job = _job()
    window = QMainWindow()
    window.batch_workspace = SimpleNamespace(controller=None, job_scroll=None, _jobs=[job])
    model = QuickBatchList(window, SimpleNamespace(_glass={}), None, window)
    model.toggleExpanded(job.job_id)
    original_text = model._rows[0]["logText"]

    for index in range(BATCH_LOG_MAX_LINES + 50):
        model.append_log(f"[JOB-001] line-{index}-" + ("x" * 4_000))

    assert len(model._logs[job.job_id]) == BATCH_LOG_MAX_LINES
    assert model._rows[0]["logText"] == original_text
    assert model._log_text_timer.isActive()

    model._log_text_timer.stop()
    model._flush_log_text()
    rendered = model._rows[0]["logText"]
    assert rendered.count("\n") == BATCH_LOG_MAX_LINES - 1
    assert len(rendered) < BATCH_LOG_MAX_LINES * (BATCH_LOG_MAX_LINE_CHARS + 50)

    # Frequent business-state publications must retain the rendered string instead
    # of rebuilding it when no new log batch has reached the UI.
    model.sync_jobs([job])
    assert model._rows[0]["logText"] is rendered


def test_controller_preview_queue_has_backpressure_and_yields_between_batches(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    del app
    controller = BatchController(tmp_path)
    emitted: list[str] = []
    controller.log.connect(emitted.append)

    for index in range(BATCH_LOG_PENDING_LINES + 50):
        controller._queue_log_preview("JOB-001", f"[JOB-001] line-{index}")

    assert len(controller._pending_log_preview) == BATCH_LOG_PENDING_LINES
    controller._log_preview_timer.stop()
    controller._flush_log_preview()
    assert len(emitted) == BATCH_LOG_FLUSH_LINES
    assert len(controller._pending_log_preview) == BATCH_LOG_PENDING_LINES - BATCH_LOG_FLUSH_LINES
    assert controller._log_preview_timer.isActive()
