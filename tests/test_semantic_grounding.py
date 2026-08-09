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


def test_text_chunks_keep_logical_identity_without_execution_grouping_api():
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
    assert catalog.logical_source_count == 2
    assert [item.logical_source_id for item in catalog.sources] == [
        "supplier:001",
        "supplier:001",
        "image:001:ccc",
    ]
    assert not hasattr(catalog, "logical_groups")


def test_grounding_catalog_includes_structured_rows_and_variant_data_without_image_url_noise(tmp_path):
    image = tmp_path / "front.jpg"
    image.write_bytes(b"fake-image-bytes-v1")
    snapshot = tmp_path / "supplier.json"
    snapshot.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "requested_url": "https://supplier.test/item",
                "final_url": "https://supplier.test/item/123",
                "title": "L11 Camera",
                "captured_at": "2026-08-08T00:00:00+00:00",
                "visible_text": "The display size is 3.0 inch and video resolution is 1080P.",
                "table_rows": [
                    {"key": "length", "value": "16", "table_index": 1, "row_index": 1},
                    {"key": "width", "value": "11", "table_index": 1, "row_index": 2},
                    {"key": "height", "value": "7", "table_index": 1, "row_index": 3},
                ],
                "json_ld": [],
                "embedded_data": ['{"sku2":"front+cabin English+German","skuId":6017876765651}'],
                "image_urls": ["https://img.test/m8.jpg"],
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
    text_sources = [item for item in catalog.sources if item.kind == TEXT_KIND]
    combined = "\n".join(item.content for item in text_sources)
    assert "display size is 3.0 inch" in combined
    assert '"key":"length","value":"16"' in combined
    assert '"key":"width","value":"11"' in combined
    assert '"key":"height","value":"7"' in combined
    assert "6017876765651" in combined
    assert "https://img.test/m8.jpg" not in combined
    assert any("#evidence=embedded" in item.origin for item in text_sources)
    assert all(item.logical_source_id == "supplier:001" for item in text_sources)
    assert all(item.source_type == "supplier_web" for item in text_sources)


def test_snapshot_text_compaction_drops_library_noise_but_preserves_disagreements(tmp_path):
    snapshot = tmp_path / "supplier.json"
    snapshot.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "requested_url": "https://supplier.test/item",
                "final_url": "https://supplier.test/item/123",
                "title": "M8",
                "captured_at": "2026-08-09T00:00:00+00:00",
                "visible_text": "Supplier text says recording resolution 720p.",
                "table_rows": [],
                "json_ld": [],
                "embedded_data": [
                    "function library(value) { return value.length + value.width; }",
                    json.dumps({"tag": "DIV", "text": "Recording resolution 1080p", "attrs": {}}),
                    json.dumps({"tag": "DIV", "text": "Recording resolution 720p", "attrs": {}}),
                    'window.context={"offerId":123,"skuId":456}',
                    'prefix window.context={"offerId":123,"skuId":456} suffix',
                ],
                "image_urls": ["https://img.test/detail.jpg"],
                "meta": {},
            }
        ),
        encoding="utf-8",
    )

    catalog = build_grounding_catalog(supplier_snapshots=(str(snapshot),))
    combined = "\n".join(item.content for item in catalog.sources if item.kind == TEXT_KIND)

    assert "value.length" not in combined
    assert "Recording resolution 720p" in combined
    assert "Recording resolution 1080p" in combined
    assert combined.count('"offerId":123') == 1
    assert combined.count('"skuId":456') == 1
    assert "https://img.test/detail.jpg" not in combined
    raw = json.loads(snapshot.read_text(encoding="utf-8"))
    assert "value.length" in raw["embedded_data"][0]
    assert raw["image_urls"] == ["https://img.test/detail.jpg"]


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
