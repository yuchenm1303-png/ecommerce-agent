from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.commerce.sync import build_authoritative_listing_success
from gui.commerce_outbox import DurableCommerceOutbox


_MIGRATION = Path("supabase/migrations/20260901160000_commerce_sync_v1.sql")
_EDGE = Path("supabase/functions/commerce-sync/index.ts")
_SYNC = Path("app/commerce/sync.py")


def _event(*, request_identity: str = "https://example.com/p/42", external_listing_id: str = "MK-9001"):
    return build_authoritative_listing_success(
        request_identity=request_identity,
        supplier_url=request_identity,
        display_name="Example Shampoo 500 ml",
        product_type_en="shampoo",
        brand="Example",
        channel="makro",
        channel_account_id="makro-account-a",
        channel_account_label="Makro Store A",
        external_listing_id=external_listing_id,
        event_id="event-001",
        occurred_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )


def test_authoritative_listing_boundary_requires_real_external_listing_id() -> None:
    with pytest.raises(ValueError, match="external_listing_id"):
        _event(external_listing_id="")

    source = _SYNC.read_text(encoding="utf-8")
    assert "makro_target_id" in source  # documented only as a forbidden substitute
    event = _event(external_listing_id="marketplace-listing-123")
    assert event["payload"]["listing"]["external_listing_id"] == "marketplace-listing-123"
    assert "makro_target_id" not in event["payload"]["listing"]


def test_product_identity_reuses_exact_source_only() -> None:
    first = _event(request_identity="https://example.com/p/42")
    same = _event(request_identity="https://example.com/p/42")
    other = _event(request_identity="https://example.com/p/43")

    assert first["payload"]["product"]["product_id"] == same["payload"]["product"]["product_id"]
    assert first["payload"]["source"]["source_product_id"] == same["payload"]["source"]["source_product_id"]
    assert first["payload"]["product"]["product_id"] != other["payload"]["product"]["product_id"]


def test_commerce_outbox_never_evicts_failed_events(tmp_path: Path) -> None:
    outbox = DurableCommerceOutbox(tmp_path)
    first_path = outbox.enqueue(_event(external_listing_id="MK-1"))
    second_event = _event(external_listing_id="MK-2")
    second_event["event_id"] = "event-002"
    outbox.enqueue(second_event)

    first = outbox.peek()
    assert first is not None and first.path == first_path
    outbox.mark_failure(first, "offline")

    assert len(outbox.pending_paths()) == 2
    retried = outbox.peek()
    assert retried is not None and retried.attempts == 1
    outbox.acknowledge(retried, event_id=retried.event_id)
    assert len(outbox.pending_paths()) == 1


def test_commerce_outbox_deduplicates_same_event_without_losing_data(tmp_path: Path) -> None:
    outbox = DurableCommerceOutbox(tmp_path)
    event = _event()
    first = outbox.enqueue(event)
    second = outbox.enqueue(dict(event))
    assert first == second
    assert len(outbox.pending_paths()) == 1

    changed = _event(external_listing_id="MK-other")
    with pytest.raises(ValueError, match="collision"):
        outbox.enqueue(changed)


def test_commerce_sync_rejects_identity_conflicts_before_first_business_write() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8").casefold()
    first_write = sql.index("insert into public.commerce_workspaces")

    assert sql.index("event_payload_conflict") < first_write
    assert sql.index("channel_account_conflict") < first_write
    assert sql.index("listing_product_conflict") < first_write
    assert sql.index("v_source_exists := found") < first_write


def test_commerce_sync_is_one_trusted_rpc_and_direct_desktop_access_stays_denied() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8").casefold()
    edge = _EDGE.read_text(encoding="utf-8")

    assert "create table if not exists public.commerce_sync_events" in sql
    assert "security definer" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "external_listing_id_required" in sql
    assert "grant execute on function public.commerce_sync_listing_success_v1" in sql
    assert "to service_role" in sql
    assert "from public, anon, authenticated" in sql

    assert 'admin.auth.getUser(token)' in edge
    assert '.from("listing_monitor_tenant_members")' in edge
    assert 'admin.rpc("commerce_sync_listing_success_v1"' in edge
    assert '.from("commerce_products")' not in edge
    assert '.from("commerce_source_products")' not in edge
    assert '.from("commerce_channel_listings")' not in edge
