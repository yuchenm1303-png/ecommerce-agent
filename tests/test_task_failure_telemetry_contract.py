from __future__ import annotations

import json
from pathlib import Path

from gui.task_failure_diagnostics import collect_workflow_failure_diagnostic


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")
TELEMETRY = (ROOT / "gui" / "usage_telemetry.py").read_text(encoding="utf-8")
MODEL = (ROOT / "gui" / "batch_model.py").read_text(encoding="utf-8")


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

    assert diagnostic["diagnostic_sources"]["process_log"] is True
    assert diagnostic["diagnostic_sources"]["execution_report"] is True
    assert diagnostic["failed_stage"] == "验证附加描述"
    assert diagnostic["error_type"] == "playwright._impl._errors.TimeoutError"
    assert "locator timed out" in diagnostic["error_message"]
    assert "Traceback (most recent call last):" in diagnostic["traceback"]
    assert "TimeoutError" in diagnostic["process_log_tail"]
    assert "super-secret" not in json.dumps(diagnostic, ensure_ascii=False)
    assert "[REDACTED]" in diagnostic["process_log_tail"]
    assert diagnostic["execution_report"]["completion"]["draft_persisted_complete"] is False
    assert len(json.dumps(diagnostic, ensure_ascii=False).encode("utf-8")) < 240_000


def test_batch_runner_persists_per_job_stage_logs_and_failure_location() -> None:
    assert "AsyncRunJournal" in RUNNER
    assert '"diagnostics" / f"{stage}.log"' in RUNNER
    assert "journal.append(line)" in RUNNER
    assert "job.failure_stage = job.stage_detail" in RUNNER
    assert "job.exit_code = exit_code" in RUNNER
    assert "failure_stage: str = \"\"" in MODEL
    assert "exit_code: int | None = None" in MODEL


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
