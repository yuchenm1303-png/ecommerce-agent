"""Regression contracts for durable audit delivery and immutable revisions."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_durable_outbox_ack_and_retry_survive_restart(tmp_path):
    module_path = ROOT / "gui" / "telemetry_outbox.py"
    spec = importlib.util.spec_from_file_location("isolated_telemetry_outbox", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    outbox = module.DurableTelemetryOutbox(tmp_path)
    request = {"action": "task_audit", "audit": {"id": "123", "status": "failed"}}
    path = outbox.enqueue(request)
    outbox.mark_failure(outbox.peek(), "http=503")
    restarted = module.DurableTelemetryOutbox(tmp_path)
    entry = restarted.peek()
    assert entry and entry.request == request
    assert entry.attempts == 1 and "503" in entry.last_error
    restarted.acknowledge(path)
    assert restarted.peek() is None


def test_all_task_publishers_use_acknowledged_outbox():
    delivery = _read("gui/telemetry_delivery.py")
    usage = _read("gui/usage_telemetry.py")
    batch = _read("gui/batch_link_telemetry.py")
    assert 'self.outbox.acknowledge(item.path)' in delivery
    assert 'response.get("accepted") is True' in delivery
    assert 'QTimer.singleShot(0, self.drain)' in delivery
    assert 'licensed_user' in delivery and 'licensed_device' in delivery
    assert 'self.delivery.enqueue({"action": action, "audit": payload["audit"]})' in usage
    assert '"action": "task_log_chunk"' in usage
    assert '"action": "task_log_chunk"' in batch
    assert 'self.delivery.enqueue({"action": "task_audit"' in batch
    assert 'reply.finished.connect(reply.deleteLater)' not in batch[batch.index('def _post_audit'):]


def test_server_accepts_log_chunks_and_revision_history_is_immutable():
    edge = _read("supabase/functions/portal-telemetry/index.ts")
    history = _read("supabase/functions/portal-task-history/index.ts")
    admin = _read("supabase/functions/portal-usage-admin/index.ts")
    migration = _read("supabase/migrations/20261010070000_listing_task_audit_revisions.sql")
    assert 'if (action === "task_log_chunk")' in edge
    assert '"task_audit_not_ready"' in edge
    assert 'textValue(body.view) === "revisions"' in history
    assert '.order("id", { ascending: false })' in history
    assert 'task_revisions: revisionHistory' in admin
    assert "after insert or update on public.listing_task_audits" in migration
    assert "is not distinct from" in migration
    assert "on delete cascade" not in migration



def test_event_queue_and_ack_contract():
    usage = _read("gui/usage_telemetry.py")
    delivery = _read("gui/telemetry_delivery.py")
    edge = _read("supabase/functions/portal-telemetry/index.ts")
    schema = _read("supabase/migrations/20261010230000_listing_usage_event_delivery_idempotency.sql")
    assert '"event_type": event_type, "outcome": outcome' in usage
    assert '"client_event_id": str(uuid.uuid4())' in usage
    assert 'if action not in {"task_audit", "task_log_chunk", "event"}' in delivery
    assert "onConflict: \"client_event_id\"" in edge
    assert "client_event_id uuid" in schema


def test_ui_startup_exceptions_are_not_silently_swallowed():
    for file in ("gui/batch_workspace.py", "gui/listing_offer_hardening.py",
                 "gui/batch_individual_controls.py", "gui/product_input_window.py"):
        assert 'record_startup_failure(' in _read(file), file
