from __future__ import annotations

from app.product_identity import build_product_identity_sources
from app.source_snapshot import SourceSnapshot


def _snapshot(*, input_mode: str = "", visible_text: str = "") -> SourceSnapshot:
    return SourceSnapshot(
        requested_url="https://example.invalid/item",
        final_url="https://example.invalid/item",
        title="Generic page title",
        captured_at="2026-08-14T00:00:00+00:00",
        visible_text=visible_text,
        meta={"input_mode": input_mode} if input_mode else {},
    )


def test_customer_pack_curated_text_is_product_identity_evidence() -> None:
    snapshot = _snapshot(
        input_mode="customer_product_pack",
        visible_text="M8 dual-channel dash camera. Brand: Acme. Recording resolution: 4K.",
    )

    sources = build_product_identity_sources(snapshot)
    by_id = {str(item["source_id"]): item for item in sources}

    assert "identity:customer-pack-text" in by_id
    assert by_id["identity:customer-pack-text"]["source_type"] == "customer_product_document"
    assert "dash camera" in str(by_id["identity:customer-pack-text"]["content"])
    assert "identity:page-title" not in by_id


def test_supplier_generic_visible_body_stays_out_of_identity_boundary() -> None:
    snapshot = _snapshot(
        visible_text="Marketplace navigation procurement slogan unrelated generic page body",
    )

    sources = build_product_identity_sources(snapshot)
    contents = "\n".join(str(item.get("content") or "") for item in sources)

    assert "identity:page-title" in {str(item["source_id"]) for item in sources}
    assert "procurement slogan" not in contents
