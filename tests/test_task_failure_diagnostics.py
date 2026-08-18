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
