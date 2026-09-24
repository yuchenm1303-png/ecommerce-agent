from __future__ import annotations

from pathlib import Path

from app import source_capture, source_capture_cache
from app.source_capture_cache import SourceCachePublishResult
from app.source_snapshot import SourceSnapshot, source_snapshot_from_json, write_source_snapshot


def _write_capture_tree(root: Path, source_url: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    write_source_snapshot(
        SourceSnapshot(
            requested_url=source_url,
            final_url=source_url,
            title="fixture product",
            captured_at="2026-08-31T00:00:00+00:00",
            visible_text="fixture evidence " * 220,
        ),
        root / "source-snapshot.json",
    )
    images = root / "product-images"
    images.mkdir()
    (images / "source-image-01.jpg").write_bytes(b"fixture-image")
    return root


def test_cache_publish_uses_immutable_generation_and_atomic_pointer(tmp_path) -> None:
    source_url = "https://supplier.example/item/42?sku=blue"
    source_dir = _write_capture_tree(tmp_path / "captured", source_url)
    cache_dir = tmp_path / "cache"

    result = source_capture._refresh_capture_cache(source_url, source_dir, cache_dir)

    assert result.published is True
    key = source_capture._source_cache_key(source_url)
    pointer = cache_dir / f"{key}.current"
    generation_name = pointer.read_text(encoding="utf-8").strip()
    generation = cache_dir / generation_name
    assert generation.is_dir()
    assert generation_name.startswith(f"{key}.")
    assert (generation / "source-snapshot.json").is_file()
    assert not (cache_dir / f".{key}.tmp").exists()

    cached = source_capture._cached_capture(
        source_url,
        output_dir=tmp_path / "materialized",
        cache_dir=cache_dir,
        cache_ttl_seconds=3600,
    )
    assert cached is not None
    assert cached.cache_hit is True
    assert cached.snapshot.requested_url == source_url
    assert [path.read_bytes() for path in cached.product_image_paths] == [b"fixture-image"]


def test_cache_pointer_permission_error_is_nonfatal_and_keeps_capture(tmp_path, monkeypatch) -> None:
    source_url = "https://supplier.example/item/42?sku=blue"
    source_dir = _write_capture_tree(tmp_path / "captured", source_url)
    cache_dir = tmp_path / "cache"

    def deny_replace(_source, _target):
        raise PermissionError(5, "access denied")

    monkeypatch.setattr(source_capture_cache.os, "replace", deny_replace)
    monkeypatch.setattr(source_capture_cache.time, "sleep", lambda _seconds: None)

    result = source_capture._refresh_capture_cache(source_url, source_dir, cache_dir)

    assert result.published is False
    assert "PermissionError" in result.detail
    assert (source_dir / "source-snapshot.json").is_file()
    assert (source_dir / "product-images" / "source-image-01.jpg").is_file()
    key = source_capture._source_cache_key(source_url)
    assert not (cache_dir / f"{key}.current").exists()


def test_locked_or_corrupt_cache_materialization_becomes_clean_miss(tmp_path, monkeypatch) -> None:
    source_url = "https://supplier.example/item/42?sku=blue"
    source_dir = _write_capture_tree(tmp_path / "captured", source_url)
    cache_dir = tmp_path / "cache"
    published = source_capture._refresh_capture_cache(source_url, source_dir, cache_dir)
    assert published.published is True

    def deny_copy(*_args, **_kwargs):
        raise PermissionError(5, "locked by another process")

    monkeypatch.setattr(source_capture_cache.shutil, "copy2", deny_copy)
    output_dir = tmp_path / "materialized"

    cached = source_capture._cached_capture(
        source_url,
        output_dir=output_dir,
        cache_dir=cache_dir,
        cache_ttl_seconds=3600,
    )

    assert cached is None
    assert not (output_dir / "source-snapshot.json").exists()
    assert not (output_dir / "source-page.png").exists()
    assert not (output_dir / "product-images").exists()


def test_live_capture_result_survives_cache_publication_failure(tmp_path, monkeypatch) -> None:
    source_url = "https://supplier.example/item/42?sku=blue"
    target = _write_capture_tree(tmp_path / "captured", source_url)
    snapshot_path = target / "source-snapshot.json"
    snapshot = source_snapshot_from_json(snapshot_path)
    captured = source_capture.CapturedProductSource(
        snapshot_path=snapshot_path,
        screenshot_path=target / "source-page.png",
        snapshot=snapshot,
        launched_now=False,
        product_image_paths=(target / "product-images" / "source-image-01.jpg",),
        cache_hit=False,
    )

    monkeypatch.setattr(
        source_capture._engine,
        "capture_product_source",
        lambda *_args, **_kwargs: captured,
    )
    monkeypatch.setattr(
        source_capture,
        "_refresh_capture_cache",
        lambda *_args, **_kwargs: SourceCachePublishResult(
            False,
            "PermissionError: [WinError 5] access denied",
        ),
    )

    result = source_capture._capture_once(
        source_url,
        output_dir=target,
        profile_dir=tmp_path / "profile",
        cdp_port=9333,
        initial_wait_ms=0,
        scroll_wait_ms=0,
        max_scroll_steps=1,
        max_visible_text_chars=1000,
        use_current_page=False,
        cache_dir=tmp_path / "cache",
        cache_ttl_seconds=900,
        force_refresh=True,
    )

    assert result is captured
    assert result.snapshot_path.is_file()
