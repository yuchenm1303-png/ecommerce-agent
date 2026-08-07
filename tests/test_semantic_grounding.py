from __future__ import annotations

import json

from app.semantic_grounding import (
    IMAGE_KIND,
    TEXT_KIND,
    build_grounding_catalog,
    chunk_text,
)


def test_chunk_text_is_bounded_and_overlapped():
    text = " ".join(f"word-{index}" for index in range(500))
    chunks = chunk_text(text, max_chars=700, overlap_chars=80)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 700 for chunk in chunks)
    # The chunker moves the next start backward, so adjacent chunks should share
    # at least one whole word for ordinary whitespace-delimited prose.
    assert set(chunks[0].split()) & set(chunks[1].split())


def test_grounding_catalog_builds_stable_image_and_snapshot_sources(tmp_path):
    snapshot = tmp_path / "supplier.json"
    snapshot.write_text(
        json.dumps(
            {
                "requested_url": "https://supplier.test/item",
                "final_url": "https://supplier.test/item/123",
                "title": "L11 Camera",
                "captured_at": "2026-08-08T00:00:00+00:00",
                "visible_text": "The display size is 3.0 inch and video resolution is 1080P.",
                "table_rows": [],
                "json_ld": [],
                "meta": {"description": "Vehicle camera product page"},
            }
        ),
        encoding="utf-8",
    )

    catalog = build_grounding_catalog(
        image_paths=(str(tmp_path / "front.jpg"),),
        supplier_snapshots=(str(snapshot),),
        max_text_chars=700,
        overlap_chars=50,
    )

    assert catalog.sources[0].source_id == "image:001"
    assert catalog.sources[0].kind == IMAGE_KIND
    text_sources = [item for item in catalog.sources if item.kind == TEXT_KIND]
    assert text_sources
    assert text_sources[0].source_id == "supplier:001:text:0001"
    assert text_sources[0].source_type == "supplier_web"
    assert "display size is 3.0 inch" in text_sources[0].content
    assert catalog.by_id("supplier:001:text:0001") is text_sources[0]


def test_grounding_manifest_does_not_lose_origin(tmp_path):
    catalog = build_grounding_catalog(
        image_paths=(str(tmp_path / "front.jpg"),),
        supplemental_text="Model: L11\nScreen: 3.0 inch",
        max_text_chars=700,
        overlap_chars=50,
    )

    manifest = catalog.as_manifest()
    assert manifest["source_count"] == 2
    assert manifest["sources"][0]["origin"].endswith("front.jpg")
    assert manifest["sources"][1]["origin"] == "supplemental_text"
