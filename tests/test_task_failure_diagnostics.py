from __future__ import annotations

import json

from gui.task_failure_diagnostics import (
    collect_workflow_failure_diagnostic,
    sanitize_telemetry_text,
    sanitize_telemetry_value,
)


def test_sanitizes_secret_query_values_without_losing_supplier_context() -> None:
    source = (
        "https://detail.1688.com/offer/930169095284.html"
        "?kj_agent_plugin=aliprice&fromkv=xytTrace:123&token=super-secret/value"
    )

    cleaned = sanitize_telemetry_text(source)

    assert "super-secret" not in cleaned
    assert "token=[REDACTED]" in cleaned
    assert "kj_agent_plugin=aliprice" in cleaned
    assert "fromkv=xytTrace:123" in cleaned

    nested = sanitize_telemetry_value(
        {
            "supplier_url": source,
            "api_key": "must-never-leak",
            "error": f"failed while opening {source}",
        }
    )
    encoded = json.dumps(nested, ensure_ascii=False)
    assert "must-never-leak" not in encoded
    assert "super-secret" not in encoded
    assert "[REDACTED]" in encoded


def test_collects_failed_workflow_event_traceback_timeline_and_manifest(tmp_path) -> None:
    run_dir = tmp_path / "workflow-full-20260818-095026-334371"
    run_dir.mkdir()
    supplier_url = "https://detail.1688.com/offer/930169095284.html?token=do-not-upload"
    (run_dir / "run-manifest.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "mode": "full",
                "product_url": supplier_url,
            }
        ),
        encoding="utf-8",
    )
    events = [
        {
            "ts": "2026-08-18T01:50:30+00:00",
            "seq": 1,
            "stage": "source_capture",
            "event": "COMPLETE",
            "elapsed_s": 8.579,
            "product_url": supplier_url,
        },
        {
            "ts": "2026-08-18T01:50:40+00:00",
            "seq": 2,
            "stage": "source",
            "event": "FAILED",
            "mode": "full",
            "ui_phase": "scan",
            "error_type": "OpenAICompatibleTransportError",
            "error": "HTTP 400 Arrearage",
            "traceback": "Traceback (most recent call last):\nboom",
            "active_stages": ["diagnostics", "workflow", "source", "listing_bootstrap"],
            "elapsed_s": 9.954,
            "product_url": supplier_url,
        },
    ]
    (run_dir / "workflow-diagnostics.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )

    diagnostic = collect_workflow_failure_diagnostic(run_dir)

    assert diagnostic["run_id"] == run_dir.name
    assert diagnostic["workflow_mode"] == "full"
    assert diagnostic["failed_stage"] == "source"
    assert diagnostic["ui_phase"] == "scan"
    assert diagnostic["error_type"] == "OpenAICompatibleTransportError"
    assert diagnostic["error_message"] == "HTTP 400 Arrearage"
    assert "Traceback" in diagnostic["traceback"]
    assert diagnostic["active_stages"][-1] == "listing_bootstrap"
    assert diagnostic["diagnostic_source_available"] is True
    assert len(diagnostic["timeline"]) == 2
    assert diagnostic["timeline"][-1]["traceback"] == "[see failure_diagnostic.traceback]"
    encoded = json.dumps(diagnostic, ensure_ascii=False)
    assert "do-not-upload" not in encoded
    assert "[REDACTED]" in encoded


def test_single_gui_workflow_log_is_discovered_as_canonical_failure_truth(tmp_path) -> None:
    run_dir = tmp_path / "workflow-full-20260830-084806-062232"
    run_dir.mkdir()
    (run_dir / "run-manifest.json").write_text(
        json.dumps({"status": "failed", "mode": "full"}),
        encoding="utf-8",
    )
    failed_event = {
        "ts": "2026-08-30T00:50:49.684+00:00",
        "stage": "step3",
        "event": "FAILED",
        "mode": "full",
        "ui_phase": "plan",
        "error_type": "RuntimeError",
        "error": "STEP 3 CURRENT RESOLVER · COLD failed with exit code 1",
        "active_stages": ["diagnostics", "workflow", "step3", "cold_resolver"],
    }
    (run_dir / "workflow-diagnostics.jsonl").write_text(
        json.dumps(failed_event) + "\n",
        encoding="utf-8",
    )
    (run_dir / "gui-workflow.log").write_text(
        "===== STEP 3 CURRENT RESOLVER · COLD =====\n"
        "Traceback (most recent call last):\n"
        "  File \"makro_resolve_ai.py\", line 99, in main\n"
        "ValueError: resolver failed token=child-secret\n"
        "Traceback (most recent call last):\n"
        "  File \"makro_gui_workflow.py\", line 575, in _run\n"
        "RuntimeError: STEP 3 CURRENT RESOLVER · COLD failed with exit code 1\n",
        encoding="utf-8",
    )

    diagnostic = collect_workflow_failure_diagnostic(
        run_dir,
        fallback_error="STEP 3 CURRENT RESOLVER · COLD failed with exit code 1",
        fallback_error_type="TaskFailure",
        fallback_stage="listing_prepare",
        workflow_mode="full",
    )

    assert diagnostic["truth_source"] == "stage_log"
    assert diagnostic["failed_stage"] == "step3"
    assert diagnostic["stage_log_name"] == "gui-workflow.log"
    assert diagnostic["process_log_name"] == "gui-workflow.log"
    assert diagnostic["available_stage_logs"] == ["gui-workflow.log"]
    assert diagnostic["diagnostic_sources"]["stage_log"] is True
    assert diagnostic["line_count"] == 7
    assert diagnostic["byte_count"] > 0
    assert len(diagnostic["sha256"]) == 64
    assert diagnostic["traceback_count"] == 2
    assert diagnostic["exception_count"] == 2
    assert diagnostic["exceptions"][0]["error_type"] == "ValueError"
    encoded = json.dumps(diagnostic, ensure_ascii=False)
    assert "child-secret" not in encoded
    assert "token=[REDACTED]" in encoded
