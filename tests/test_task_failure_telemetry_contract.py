from __future__ import annotations

import json
from pathlib import Path

from gui.task_failure_diagnostics import collect_workflow_failure_diagnostic


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")
TELEMETRY = (ROOT / "gui" / "usage_telemetry.py").read_text(encoding="utf-8")
MODEL = (ROOT / "gui" / "batch_model.py").read_text(encoding="utf-8")
BATCH_JOB = (ROOT / "makro_batch_job.py").read_text(encoding="utf-8")


def test_failure_diagnostic_keeps_newest_real_process_exception_and_redacts_secret(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    (workflow / "run-manifest.json").write_text(
        json.dumps({"run_id": "run-123", "mode": "full", "makro_target_id": "TARGET"}),
        encoding="utf-8",
    )
    log = tmp_path / "execute.log"
    log.write_text(
        ("old line\n" * 12000)
        + "Additional Description: completed\n"
        + "Traceback (most recent call last):\n"
        + "  File \"makro_execute_listing.py\", line 999, in main\n"
        + "playwright._impl._errors.TimeoutError: locator timed out "
        + "https://example.com/?token=super-secret\n",
        encoding="utf-8",
    )

    report_root = tmp_path / "real-execution" / "execute-20260818-120000"
    report_root.mkdir(parents=True)
    (report_root / "report.json").write_text(
        json.dumps(
            {
                "completion": {"draft_persisted_complete": False},
                "section_reports": [{"section": "Additional Description", "status": "failed"}],
                "send_to_qc_clicked": False,
            }
        ),
        encoding="utf-8",
    )

    diagnostic = collect_workflow_failure_diagnostic(
        workflow,
        fallback_error="Real execution exit code=1",
        fallback_error_type="BatchJobFailure",
        fallback_stage="验证附加描述",
        workflow_mode="full",
        process_log_path=log,
        artifact_roots=(tmp_path / "real-execution",),
    )

    assert diagnostic["schema"] == 3
    assert diagnostic["diagnostic_sources"]["process_log"] is True
    assert diagnostic["diagnostic_sources"]["execution_report"] is True
    assert diagnostic["failed_stage"] == "验证附加描述"
    assert diagnostic["error_type"] == "playwright._impl._errors.TimeoutError"
    assert "locator timed out" in diagnostic["error_message"]
    assert "Traceback (most recent call last):" in diagnostic["traceback"]
    assert "TimeoutError" in diagnostic["process_log_tail"]
    assert "execute.log" in diagnostic["process_log_files"]
    assert "super-secret" not in json.dumps(diagnostic, ensure_ascii=False)
    assert "[REDACTED]" in diagnostic["process_log_tail"]
    assert diagnostic["execution_report"]["completion"]["draft_persisted_complete"] is False
    assert len(json.dumps(diagnostic, ensure_ascii=False).encode("utf-8")) < 240_000


def test_batch_failure_discovers_real_prepare_log_even_when_phase_points_to_missing_execute_log(tmp_path: Path) -> None:
    workflow = tmp_path / "jobs" / "JOB-002" / "workflow"
    workflow.mkdir(parents=True)
    diagnostics = workflow.parent / "diagnostics"
    diagnostics.mkdir()
    prepare_log = diagnostics / "prepare.log"
    prepare_log.write_text(
        "STEP 3 CURRENT READ-ONLY FILL PLAN\n"
        "Traceback (most recent call last):\n"
        "  File \"makro_plan_listing.py\", line 220, in main\n"
        "RuntimeError: live schema mismatch\n",
        encoding="utf-8",
    )

    diagnostic = collect_workflow_failure_diagnostic(
        workflow,
        fallback_error="Prepare exit code=1",
        fallback_error_type="BatchJobFailure",
        fallback_stage="解析字段",
        workflow_mode="full",
        process_log_path=diagnostics / "execute.log",
    )

    assert diagnostic["diagnostic_sources"]["process_log"] is True
    assert diagnostic["process_log_name"] == "prepare.log"
    assert diagnostic["process_log_files"] == ["prepare.log"]
    assert "live schema mismatch" in diagnostic["process_log_tail"]
    assert diagnostic["error_type"] == "RuntimeError"
    assert "makro_plan_listing.py" in diagnostic["traceback"]


def test_batch_runner_persists_per_job_stage_logs_and_failure_location() -> None:
    assert "AsyncRunJournal" in RUNNER
    assert '"diagnostics" / f"{stage}.log"' in RUNNER
    assert "journal.append(line)" in RUNNER
    assert "job.failure_stage = job.stage_detail" in RUNNER
    assert "job.exit_code = exit_code" in RUNNER
    assert "failure_stage: str = \"\"" in MODEL
    assert "exit_code: int | None = None" in MODEL


def test_batch_prepare_keeps_full_python_traceback_in_the_local_log() -> None:
    assert "import traceback" in BATCH_JOB
    assert BATCH_JOB.count("traceback.print_exc()") >= 2


def test_owner_telemetry_uses_independent_product_audits_with_real_failure_evidence() -> None:
    assert '"audit_scope": "batch_link"' in TELEMETRY
    assert "self._batch_audit_ids: dict[str, str]" in TELEMETRY
    assert "def _batch_job_result(" in TELEMETRY
    assert "process_log_path=_batch_process_log(job, event_type)" in TELEMETRY
    assert "artifact_roots=artifact_roots" in TELEMETRY
    assert 'result["failure_diagnostic"] = collect_workflow_failure_diagnostic(' in TELEMETRY
    assert "_BATCH_FAILURE_DIAGNOSTIC_LIMIT" not in TELEMETRY


def test_single_execute_failure_uses_real_execution_gui_log() -> None:
    assert 'return str(Path(output_root) / "real-execution-gui.log")' in TELEMETRY
    assert "process_log_path=_runner_process_log(runner)" in TELEMETRY
    assert "artifact_roots=_runner_artifact_roots(runner)" in TELEMETRY
