from pathlib import Path

import pytest

from app.source_capture_cache import publish_source_capture_cache, read_source_capture_cache
from app.source_snapshot import SourceSnapshot, write_source_snapshot


@pytest.mark.parametrize(
    "source_url",
    [
        "https://www.amazon.co.za/dp/B0TEST123",
        "https://detail.1688.com/offer/850845635717.html",
    ],
)
def test_supplier_image_count_survives_prefetch_to_batch_cache(tmp_path: Path, source_url: str) -> None:
    source_dir = tmp_path / "live-source"
    source_dir.mkdir()
    write_source_snapshot(
        SourceSnapshot(
            requested_url=source_url,
            final_url=source_url,
            title="fixture product",
            captured_at="2026-09-24T00:00:00Z",
        ),
        source_dir / "source-snapshot.json",
    )
    image_dir = source_dir / "product-images"
    image_dir.mkdir()
    for index in range(1, 4):
        (image_dir / f"source-image-{index:02d}.jpg").write_bytes(f"image-{index}".encode())

    live_count = len(tuple(image_dir.glob("*")))
    cache_dir = tmp_path / "cache"
    cache_key = "image-handoff-fixture"
    published = publish_source_capture_cache(
        source_url,
        cache_key=cache_key,
        source_dir=source_dir,
        cache_dir=cache_dir,
    )
    assert published.published is True

    materialized = read_source_capture_cache(
        source_url,
        cache_key=cache_key,
        output_dir=tmp_path / "batch-source",
        cache_dir=cache_dir,
        cache_ttl_seconds=3600,
    )
    assert materialized is not None
    assert len(materialized.product_image_paths) == live_count == 3
