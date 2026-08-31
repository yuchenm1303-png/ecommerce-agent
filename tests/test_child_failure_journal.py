from __future__ import annotations

from pathlib import Path

import pytest

from app.failure_contract import run_cli_with_failure_journal
from gui.task_failure_diagnostics import collect_workflow_failure_diagnostic


ROOT = Path(__file__).resolve().parents[1]


def _raise_runtime(message: str):
    def _main() -> int:
        raise RuntimeError(message)

    return _main


def test_single_step3_child_tracebacks_are_durable_and_all_append(tmp_path: Path) -> None:
    run_dir = tmp_path / "workflow-full-test"

    with pytest.raises(RuntimeError, match="cold resolver root cause"):
        run_cli_with_failure_journal(
            _raise_runtime("cold resolver root cause"),
            stage="makro_resolve_ai",
            argv=["--output-dir", str(run_dir / "02-cold-resolver")],
        )

    with pytest.raises(ValueError, match="planner root cause"):
        def _planner() -> int:
            raise ValueError("planner root cause")

        run_cli_with_failure_journal(
            _planner,
            stage="makro_plan_listing",
            argv=["--output-dir", str(run_dir / "04-fill-plan")],
        )

    stage_log = run_dir / "diagnostics" / "prepare.log"
    text = stage_log.read_text(encoding="utf-8")
    assert text.count("===== CHILD FAILURE") == 2
    assert "Traceback (most recent call last):" in text
    assert "RuntimeError: cold resolver root cause" in text
    assert "ValueError: planner root cause" in text

    diagnostic = collect_workflow_failure_diagnostic(
        run_dir,
        fallback_error="STEP 3 CURRENT RESOLVER · COLD failed with exit code 1",
        fallback_error_type="RuntimeError",
        fallback_stage="step3",
    )

    assert diagnostic["truth_source"] == "stage_log"
    assert diagnostic["stage_log_name"] == "prepare.log"
    assert diagnostic["diagnostic_sources"]["stage_log"] is True
    assert diagnostic["line_count"] > 0
    assert diagnostic["byte_count"] == len(text.encode("utf-8"))
    assert len(diagnostic["sha256"]) == 64
    messages = [str(item.get("message") or "") for item in diagnostic["exceptions"]]
    assert any("cold resolver root cause" in message for message in messages)
    assert any("planner root cause" in message for message in messages)
    assert diagnostic["exception_count"] >= 2
    assert diagnostic["traceback_count"] >= 2
    assert "prepare.log" in diagnostic["process_log_files"]


def test_single_step3_nonzero_child_exit_is_not_reduced_to_parent_exit_code(tmp_path: Path) -> None:
    run_dir = tmp_path / "workflow-full-test"

    status = run_cli_with_failure_journal(
        lambda: 7,
        stage="makro_resolve_ai",
        argv=["--output-dir", str(run_dir / "03-hot-resolver")],
    )

    assert status == 7
    text = (run_dir / "diagnostics" / "prepare.log").read_text(encoding="utf-8")
    assert "ChildProcessExitError: makro_resolve_ai returned exit code 7" in text


def test_single_step3_child_clis_share_the_failure_journal_contract() -> None:
    resolver_source = (ROOT / "makro_resolve_ai.py").read_text(encoding="utf-8")
    planner_source = (ROOT / "makro_plan_listing.py").read_text(encoding="utf-8")

    assert "run_cli_with_failure_journal" in resolver_source
    assert 'stage="makro_resolve_ai"' in resolver_source
    assert "run_cli_with_failure_journal" in planner_source
    assert 'stage="makro_plan_listing"' in planner_source
