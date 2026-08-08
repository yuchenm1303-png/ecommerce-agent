from __future__ import annotations

import json

from app.semantic_grounding import (
    IMAGE_KIND,
    TEXT_KIND,
    GroundedSource,
    GroundingCatalog,
    build_grounding_catalog,
    chunk_text,
)


def test_chunk_text_is_bounded_and_overlapped():
    text = " ".join(f"word-{index}" for index in range(500))
    chunks = chunk_text(text, max_chars=700, overlap_chars=80)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 700 for chunk in chunks)
    assert set(chunks[0].split()) & set(chunks[1].split())


def test_text_chunks_share_one_logical_source_but_keep_exact_citation_ids():
    catalog = GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:001:text:0001:aaa",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item",
                content="part one",
            ),
            GroundedSource(
                source_id="supplier:001:text:0002:bbb",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item",
                content="part two",
            ),
            GroundedSource(
                source_id="image:001:ccc",
                source_type="product_image",
                kind=IMAGE_KIND,
                origin="front.jpg",
                image_path="front.jpg",
            ),
        ]
    )

    groups = catalog.logical_groups()
    assert catalog.logical_source_count == 2
    assert [key for key, _ in groups] == ["supplier:001", "image:001:ccc"]
    assert [item.source_id for item in groups[0][1].sources] == [
        "supplier:001:text:0001:aaa",
        "supplier:001:text:0002:bbb",
    ]


def test_grounding_catalog_builds_content_bound_image_and_snapshot_sources(tmp_path):
    image = tmp_path / "front.jpg"
    image.write_bytes(b"fake-image-bytes-v1")
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
        image_paths=(str(image),),
        supplier_snapshots=(str(snapshot),),
        max_text_chars=700,
        overlap_chars=50,
    )

    assert catalog.sources[0].source_id.startswith("image:001:")
    assert len(catalog.sources[0].sha256) == 64
    assert catalog.sources[0].kind == IMAGE_KIND
    text_sources = [item for item in catalog.sources if item.kind == TEXT_KIND]
    assert text_sources
    assert text_sources[0].source_id.startswith("supplier:001:text:0001:")
    assert text_sources[0].logical_source_id == "supplier:001"
    assert text_sources[0].source_type == "supplier_web"
    assert len(text_sources[0].sha256) == 64
    assert "display size is 3.0 inch" in text_sources[0].content
    assert catalog.by_id(text_sources[0].source_id) is text_sources[0]


def test_source_id_changes_when_image_bytes_change(tmp_path):
    image = tmp_path / "front.jpg"
    image.write_bytes(b"image-v1")
    first = build_grounding_catalog(image_paths=(str(image),)).sources[0]

    image.write_bytes(b"image-v2")
    second = build_grounding_catalog(image_paths=(str(image),)).sources[0]

    assert first.source_id != second.source_id
    assert first.sha256 != second.sha256


def test_grounding_manifest_keeps_chunk_and_logical_source_identity(tmp_path):
    image = tmp_path / "front.jpg"
    image.write_bytes(b"fake-image")
    catalog = build_grounding_catalog(
        image_paths=(str(image),),
        supplemental_text="Model: L11\nScreen: 3.0 inch",
        max_text_chars=700,
        overlap_chars=50,
    )

    manifest = catalog.as_manifest()
    assert manifest["schema_version"] == 3
    assert manifest["source_count"] == 2
    assert manifest["logical_source_count"] == 2
    assert manifest["sources"][0]["origin"].endswith("front.jpg")
    assert len(manifest["sources"][0]["sha256"]) == 64
    assert manifest["sources"][1]["origin"] == "supplemental_text"
    assert manifest["sources"][1]["logical_source_id"] == "customer-text:001"
