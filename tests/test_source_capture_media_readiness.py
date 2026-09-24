from app.source_capture_acceptance import assess_source_capture
from app.source_snapshot import SourceSnapshot


def _snapshot(*, text: str = "", rows=None):
    return SourceSnapshot(
        requested_url="https://example.com/product/1",
        final_url="https://example.com/product/1",
        title="Example product",
        captured_at="2026-09-24T00:00:00+00:00",
        visible_text=text,
        table_rows=list(rows or []),
        json_ld=[],
        embedded_data=[],
        image_urls=[],
        meta={},
        warnings=[],
    )


def test_text_rich_source_can_be_semantically_ready_but_media_degraded():
    verdict = assess_source_capture(
        _snapshot(text="x" * 5000),
        product_image_count=0,
    )
    assert verdict.ready is True
    assert verdict.listing_media_ready is False
    assert "no local product image" in verdict.media_reason


def test_one_local_product_image_makes_listing_media_ready():
    verdict = assess_source_capture(
        _snapshot(text="x" * 5000),
        product_image_count=1,
    )
    assert verdict.ready is True
    assert verdict.listing_media_ready is True


def test_partial_shell_remains_semantically_unready_even_with_zero_media():
    verdict = assess_source_capture(
        _snapshot(text="loading"),
        product_image_count=0,
    )
    assert verdict.ready is False
    assert verdict.listing_media_ready is False
