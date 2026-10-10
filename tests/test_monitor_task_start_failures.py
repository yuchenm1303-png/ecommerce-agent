from __future__ import annotations

from types import SimpleNamespace

from gui.usage_telemetry import UsageTelemetryController


def _fixture():
    events, audits = [], []
    sink = SimpleNamespace(
        _enabled=lambda: True,
        _event=lambda *args: events.append(args),
        _task_audit=lambda *args, **kw: audits.append((args, kw)),
    )
    return sink, events, audits


def test_ui_start_exception_generates_real_failed_audit_even_without_running_signal():
    sink, events, audits = _fixture()
    UsageTelemetryController.record_startup_failure(
        sink, "batch", "batch_prepare", "missing prerequisite",
        source_batch_id="old-batch-not-unique",
    )
    assert events == [("batch_prepare", "failed")]
    assert len(audits) == 1
    audit_id, data = audits[0]
    assert audit_id
    assert data["task_kind"] == "batch"
    assert data["status"] == "failed"
    assert data["result_data"]["failure_stage"] == "before_running"
    assert data["input_data"]["source_batch_id"] == "old-batch-not-unique"
    assert "batch_id" not in data["input_data"]  # Don't overwrite an old job.
    assert "missing prerequisite" in data["error_text"]


def test_batch_failed_signal_without_running_is_recorded():
    sink, events, audits = _fixture()
    sink._batch_event_type = ""
    sink.window = SimpleNamespace(batch_workspace=SimpleNamespace(
        controller=SimpleNamespace(batch=SimpleNamespace(batch_id="old-batch"))
    ))
    sink.record_startup_failure = lambda *a, **k: UsageTelemetryController.record_startup_failure(sink, *a, **k)
    UsageTelemetryController._on_batch_failed(sink, "Initialization error")
    assert events == [("batch_prepare", "failed")]
    assert len(audits) == 1
    assert audits[0][1]["error_text"] == "Initialization error"


def test_single_prepare_failure_before_running_keeps_evidence():
    sink, events, audits = _fixture()
    sink.window = SimpleNamespace(runner=None)
    sink._prepare_active = False
    sink._single_audit_id = ""
    sink._single_started_at = ""
    sink._single_input = {}
    sink._single_result = {}
    sink._single_input_snapshot = lambda: {"supplier_url": "https://example.org/item"}
    sink._enqueue_single_logs = lambda phase: None
    UsageTelemetryController._on_prepare_failed(sink, "Preflight exception")
    assert len(audits) == 1
    assert events == [("listing_prepare", "failed")]
    assert audits[0][1]["status"] == "failed"
    assert audits[0][1]["phase"] == "listing_prepare"
    assert audits[0][1]["completed_at"]
