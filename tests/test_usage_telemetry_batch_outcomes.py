from __future__ import annotations

from types import SimpleNamespace

from gui.usage_telemetry import (
    _batch_job_phase,
    _batch_operation_job_ids,
    _batch_terminal_semantics,
)


def _batch(status: str, *job_states: str) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        jobs=[
            SimpleNamespace(job_id=f"JOB-{index:03d}", status=job_status)
            for index, job_status in enumerate(job_states, start=1)
        ],
    )


def _job(
    status: str,
    *,
    progress: int = 0,
    run_dir: str = "",
    execution_report: str = "",
    operation_phase: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        progress=progress,
        run_dir=run_dir,
        execution_report=execution_report,
        operation_phase=operation_phase,
    )


def test_batch_prepare_is_not_reported_success_when_any_product_failed() -> None:
    batch = _batch("PREPARED", "READY", "FAILED", "READY")
    cohort = _batch_operation_job_ids(batch, "batch_prepare")

    assert cohort == ("JOB-001", "JOB-002", "JOB-003")
    assert _batch_terminal_semantics("batch_prepare", batch, cohort) == ("failed", "review")


def test_batch_prepare_is_success_only_when_every_product_is_ready() -> None:
    batch = _batch("PREPARED", "READY", "READY")
    cohort = _batch_operation_job_ids(batch, "batch_prepare")

    assert _batch_terminal_semantics("batch_prepare", batch, cohort) == ("completed", "ready")


def test_batch_execute_tracks_only_products_that_were_ready_at_start() -> None:
    batch = _batch("EXECUTING", "FAILED", "READY", "READY")
    cohort = _batch_operation_job_ids(batch, "batch_execute")
    assert cohort == ("JOB-002", "JOB-003")

    batch.status = "COMPLETE"
    batch.jobs[1].status = "DONE"
    batch.jobs[2].status = "FAILED"
    assert _batch_terminal_semantics("batch_execute", batch, cohort) == ("failed", "review")


def test_successful_execute_cohort_keeps_prior_prepare_failure_visible_in_audit() -> None:
    batch = _batch("EXECUTING", "FAILED", "READY", "READY")
    cohort = _batch_operation_job_ids(batch, "batch_execute")

    batch.status = "COMPLETE"
    batch.jobs[1].status = "DONE"
    batch.jobs[2].status = "DONE"
    assert _batch_terminal_semantics("batch_execute", batch, cohort) == ("completed", "review")


def test_prepare_failure_never_inherits_global_execute_phase() -> None:
    failed_fill_plan = _job(
        "FAILED",
        progress=76,
        operation_phase="batch_prepare",
    )

    assert _batch_job_phase(failed_fill_plan, "batch_execute") == "batch_prepare"


def test_prepare_review_at_100_percent_stays_prepare_from_persisted_phase() -> None:
    prepare_review = _job(
        "REVIEW",
        progress=100,
        operation_phase="batch_prepare",
    )

    assert _batch_job_phase(prepare_review, "batch_execute") == "batch_prepare"


def test_execute_initialization_failure_stays_execute_from_persisted_phase() -> None:
    execute_failure = _job(
        "FAILED",
        progress=0,
        operation_phase="batch_execute",
    )

    assert _batch_job_phase(execute_failure, "batch_prepare") == "batch_execute"


def test_known_prepare_states_never_inherit_global_execute_phase() -> None:
    assert _batch_job_phase(_job("RESOLVING", progress=76), "batch_execute") == "batch_prepare"
    assert _batch_job_phase(_job("READY", progress=100), "batch_execute") == "batch_prepare"


def test_execute_active_and_done_states_stay_execute() -> None:
    assert _batch_job_phase(_job("FILLING", progress=82), "batch_prepare") == "batch_execute"
    assert _batch_job_phase(_job("DONE", progress=100), "batch_prepare") == "batch_execute"


def test_legacy_terminal_jobs_fall_back_to_local_execute_evidence() -> None:
    assert _batch_job_phase(_job("FAILED", progress=94), "batch_execute") == "batch_prepare"
    assert _batch_job_phase(
        _job("FAILED", progress=0, execution_report="report.json"),
        "batch_prepare",
    ) == "batch_execute"
