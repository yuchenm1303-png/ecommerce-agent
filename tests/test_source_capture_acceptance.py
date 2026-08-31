from __future__ import annotations

from pathlib import Path

import pytest

from app import source_capture
from app.source_capture_acceptance import assess_source_capture
from app.source_snapshot import SnapshotTableRow, SourceSnapshot


def _snapshot(
    *,
    text_chars: int,
    table_rows: int = 0,
    embedded_items: int = 0,
    json_ld: list[object] | None = None,
) -> SourceSnapshot:
    return SourceSnapshot(
        requested_url="https://supplier.example/item/42",
        final_url="https://supplier.example/item/42",
        title="fixture product",
        captured_at="2026-08-31T00:00:00+00:00",
        visible_text="x" * text_chars,
        table_rows=[
            SnapshotTableRow(
                key=f"k{index}",
                value=f"v{index}",
                table_index=1,
                row_index=index + 1,
            )
            for index in range(table_rows)
        ],
        embedded_data=[f"embedded-{index}" for index in range(embedded_items)],
        json_ld=list(json_ld or []),
    )


def _rich_product_jsonld() -> list[object]:
    return [
        {
            "@type": "Product",
            "name": "Fixture Product",
            "description": "A complete structured product description",
            "brand": {"@type": "Brand", "name": "Fixture"},
            "sku": "SKU-42",
            "image": ["https://cdn.example/image-1.jpg"],
            "offers": {"@type": "Offer", "price": "10.00"},
        }
    ]


def _captured(tmp_path: Path, snapshot: SourceSnapshot, image_count: int):
    return source_capture.CapturedProductSource(
        snapshot_path=tmp_path / "source-snapshot.json",
        screenshot_path=tmp_path / "source-page.png",
        snapshot=snapshot,
        launched_now=False,
        product_image_paths=tuple(
            tmp_path / f"source-image-{index:02d}.jpg"
            for index in range(1, image_count + 1)
        ),
        cache_hit=False,
    )


def test_dyson_like_partial_shell_is_rejected_even_with_rich_jsonld() -> None:
    verdict = assess_source_capture(
        _snapshot(
            text_chars=536,
            table_rows=0,
            embedded_items=0,
            json_ld=_rich_product_jsonld(),
        ),
        product_image_count=2,
    )

    assert verdict.ready is False
    assert verdict.signal_count == 0
    assert verdict.rich_product_jsonld is True


def test_full_product_capture_is_accepted() -> None:
    verdict = assess_source_capture(
        _snapshot(
            text_chars=4_878,
            table_rows=22,
            embedded_items=1,
            json_ld=_rich_product_jsonld(),
        ),
        product_image_count=24,
    )

    assert verdict.ready is True
    assert verdict.strong_signal is True


def test_partial_capture_retries_current_page_then_recovers(tmp_path, monkeypatch) -> None:
    partial = _captured(
        tmp_path,
        _snapshot(text_chars=536, json_ld=_rich_product_jsonld()),
        2,
    )
    recovered = _captured(
        tmp_path,
        _snapshot(
            text_chars=4_878,
            table_rows=22,
            embedded_items=1,
            json_ld=_rich_product_jsonld(),
        ),
        24,
    )
    calls: list[dict[str, object]] = []
    results = iter((partial, recovered))

    def fake_capture(*_args, **kwargs):
        calls.append(dict(kwargs))
        return next(results)

    monkeypatch.setattr(source_capture._engine, "capture_product_source", fake_capture)

    result = source_capture._capture_once(
        "https://supplier.example/item/42",
        output_dir=tmp_path,
        profile_dir=tmp_path / "profile",
        cdp_port=9333,
        initial_wait_ms=1800,
        scroll_wait_ms=180,
        max_scroll_steps=120,
        max_visible_text_chars=120_000,
        use_current_page=False,
        cache_dir=None,
        cache_ttl_seconds=900,
        force_refresh=True,
    )

    assert result is recovered
    assert len(calls) == 2
    assert calls[0]["use_current_page"] is False
    assert calls[1]["use_current_page"] is True
    assert int(calls[1]["initial_wait_ms"]) >= 2_600


def test_persistently_partial_capture_fails_before_downstream(tmp_path, monkeypatch) -> None:
    partial = _captured(
        tmp_path,
        _snapshot(text_chars=536, json_ld=_rich_product_jsonld()),
        2,
    )
    calls: list[dict[str, object]] = []

    def fake_capture(*_args, **kwargs):
        calls.append(dict(kwargs))
        return partial

    monkeypatch.setattr(source_capture._engine, "capture_product_source", fake_capture)

    with pytest.raises(source_capture._engine.SourceCaptureError, match="PARTIAL_SOURCE"):
        source_capture._capture_once(
            "https://supplier.example/item/42",
            output_dir=tmp_path,
            profile_dir=tmp_path / "profile",
            cdp_port=9333,
            initial_wait_ms=1800,
            scroll_wait_ms=180,
            max_scroll_steps=120,
            max_visible_text_chars=120_000,
            use_current_page=False,
            cache_dir=None,
            cache_ttl_seconds=900,
            force_refresh=True,
        )

    assert len(calls) == 3
    assert calls[0]["use_current_page"] is False
    assert calls[1]["use_current_page"] is True
    assert calls[2]["use_current_page"] is False
    assert int(calls[2]["initial_wait_ms"]) >= 3_600
