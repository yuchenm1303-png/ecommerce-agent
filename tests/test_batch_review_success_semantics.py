from __future__ import annotations

from types import SimpleNamespace

from gui.batch_link_telemetry import _audit_status
from gui.usage_telemetry import _batch_job_status, _batch_terminal_semantics


def test_execute_review_is_completed_in_monitoring() -> None:
    assert _audit_status("batch_execute", "REVIEW") == ("completed", True)
    assert _batch_job_status("batch_execute", "REVIEW") == "completed"


def test_review_only_execute_batch_is_completed() -> None:
    batch = SimpleNamespace(
        status="COMPLETE",
        jobs=[SimpleNamespace(job_id="JOB-001", status="REVIEW")],
    )

    assert _batch_terminal_semantics(
        "batch_execute",
        batch,
        ("JOB-001",),
    ) == ("completed", "completed")


def test_prepare_review_remains_review() -> None:
    assert _audit_status("batch_prepare", "REVIEW") == ("review", True)
    assert _batch_job_status("batch_prepare", "REVIEW") == "review"
