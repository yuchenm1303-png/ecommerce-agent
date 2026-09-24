from __future__ import annotations

import json

from app.workflow_diagnostics import configure_diagnostics, diag_current_exception, diag_event


def _events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_workflow_diagnostics_writes_jsonl_redacts_secrets_and_times_stage(tmp_path, capsys) -> None:
    sink = configure_diagnostics(tmp_path, "unit-test", product_url="https://example.test/item/1")
    diag_event("source", "START", api_key="should-not-leak", image_count=3)
    diag_event("source", "COMPLETE", snapshot="source-snapshot.json")

    events = _events(sink.path)
    source_start = next(item for item in events if item["stage"] == "source" and item["event"] == "START")
    source_complete = next(item for item in events if item["stage"] == "source" and item["event"] == "COMPLETE")

    assert source_start["api_key"] == "<redacted>"
    assert source_start["image_count"] == 3
    assert source_complete["snapshot"] == "source-snapshot.json"
    assert source_complete["elapsed_s"] >= 0
    assert "WORKFLOW_DIAG" in capsys.readouterr().out


def test_workflow_diagnostics_records_handled_traceback(tmp_path, capsys) -> None:
    sink = configure_diagnostics(tmp_path, "unit-test")
    try:
        raise RuntimeError("diagnostic-boom")
    except RuntimeError:
        diag_current_exception("step2", page_url="https://seller.makro.co.za/example")

    events = _events(sink.path)
    failed = next(item for item in events if item["stage"] == "step2" and item["event"] == "FAILED")
    assert failed["error_type"] == "RuntimeError"
    assert failed["error"] == "diagnostic-boom"
    assert "RuntimeError: diagnostic-boom" in failed["traceback"]
    assert "WORKFLOW_TRACEBACK stage=step2" in capsys.readouterr().out
