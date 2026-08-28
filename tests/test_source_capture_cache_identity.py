from __future__ import annotations

from app import source_capture
from app.source_snapshot import SourceSnapshot, write_source_snapshot
from app.supplier_url_identity import supplier_request_identity


def _snapshot(url: str) -> SourceSnapshot:
    return SourceSnapshot(
        requested_url=url,
        final_url=url,
        title="fixture product",
        captured_at="2026-08-26T00:00:00+00:00",
    )


def test_source_cache_uses_shared_exact_request_identity() -> None:
    red = "https://supplier.example/item/42?sku=red&pack=1#details"
    blue = "https://supplier.example/item/42?sku=blue&pack=1#details"
    red_other_fragment = "https://SUPPLIER.EXAMPLE/item/42?sku=red&pack=1#reviews"
    red_trailing_slash = "https://supplier.example/item/42/?sku=red&pack=1#details"

    assert source_capture._canonical_source_url(red) == supplier_request_identity(red)
    assert source_capture._canonical_source_url(red_other_fragment) == supplier_request_identity(
        red_other_fragment
    )
    assert source_capture._source_cache_key(red) == source_capture._source_cache_key(
        red_other_fragment
    )
    assert source_capture._source_cache_key(red) != source_capture._source_cache_key(blue)
    assert source_capture._source_cache_key(red) != source_capture._source_cache_key(
        red_trailing_slash
    )


def test_cached_capture_rejects_snapshot_owned_by_another_variant(tmp_path) -> None:
    requested = "https://supplier.example/item/42?sku=blue"
    wrong_variant = "https://supplier.example/item/42?sku=red"
    cache_root = tmp_path / "cache"
    slot = cache_root / source_capture._source_cache_key(requested)
    slot.mkdir(parents=True)
    write_source_snapshot(_snapshot(wrong_variant), slot / "source-snapshot.json")
    (slot / "source-page.png").write_bytes(b"fixture")

    result = source_capture._cached_capture(
        requested,
        output_dir=tmp_path / "run",
        cache_dir=cache_root,
        cache_ttl_seconds=900,
    )

    assert result is None
    assert not (tmp_path / "run" / "source-snapshot.json").exists()


def test_cached_capture_rejects_snapshot_with_different_trailing_slash_identity(tmp_path) -> None:
    requested = "https://supplier.example/item/42?sku=blue"
    wrong_resource = "https://supplier.example/item/42/?sku=blue"
    cache_root = tmp_path / "cache"
    slot = cache_root / source_capture._source_cache_key(requested)
    slot.mkdir(parents=True)
    write_source_snapshot(_snapshot(wrong_resource), slot / "source-snapshot.json")
    (slot / "source-page.png").write_bytes(b"fixture")

    result = source_capture._cached_capture(
        requested,
        output_dir=tmp_path / "run",
        cache_dir=cache_root,
        cache_ttl_seconds=900,
    )

    assert result is None


def test_cached_capture_accepts_same_exact_variant_identity(tmp_path, monkeypatch) -> None:
    requested = "https://supplier.example/item/42?sku=blue#details"
    cache_root = tmp_path / "cache"
    slot = cache_root / source_capture._source_cache_key(requested)
    slot.mkdir(parents=True)
    snapshot_path = write_source_snapshot(
        _snapshot("https://supplier.example/item/42?sku=blue#source"),
        slot / "source-snapshot.json",
    )
    (slot / "source-page.png").write_bytes(b"fixture")
    fresh_time = snapshot_path.stat().st_mtime
    monkeypatch.setattr(source_capture.time, "time", lambda: fresh_time + 1.0)

    result = source_capture._cached_capture(
        requested,
        output_dir=tmp_path / "run",
        cache_dir=cache_root,
        cache_ttl_seconds=900,
    )

    assert result is not None
    assert result.cache_hit is True
    assert result.snapshot.requested_url.endswith("?sku=blue#source")
