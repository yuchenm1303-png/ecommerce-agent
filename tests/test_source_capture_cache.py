from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from app.source_capture import (
    _cached_capture,
    _screenshot_with_navigation_retry,
    _source_cache_key,
)
from app.source_snapshot import SourceSnapshot, write_source_snapshot


def test_source_cache_key_ignores_query_tracking_noise():
    clean = "https://detail.1688.com/offer/850845635717.html"
    tracked = clean + "?spm=a2615.2177701.autotrace-offerGeneral.1&from=market"
    assert _source_cache_key(clean) == _source_cache_key(tracked)


def test_source_cache_key_changes_for_different_offer():
    first = "https://detail.1688.com/offer/850845635717.html"
    second = "https://detail.1688.com/offer/850845635718.html"
    assert _source_cache_key(first) != _source_cache_key(second)


class _ViewportFallbackPage:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def screenshot(self, *, path: str, full_page: bool) -> None:
        self.calls.append(full_page)
        if full_page:
            raise PlaywrightError("Protocol error (Page.captureScreenshot): Unable to capture screenshot")
        Path(path).write_bytes(b"viewport")


class _ScreenshotFailurePage:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def screenshot(self, *, path: str, full_page: bool) -> None:
        self.calls.append(full_page)
        raise PlaywrightError("Protocol error (Page.captureScreenshot): Unable to capture screenshot")


def test_protocol_screenshot_failure_falls_back_to_viewport(tmp_path):
    page = _ViewportFallbackPage()
    target = tmp_path / "source-page.png"

    ok, note = _screenshot_with_navigation_retry(page, target, settle_ms=0)

    assert ok is True
    assert page.calls == [True, False]
    assert target.read_bytes() == b"viewport"
    assert "viewport fallback succeeded" in note


def test_both_screenshot_modes_fail_without_raising(tmp_path):
    page = _ScreenshotFailurePage()
    target = tmp_path / "source-page.png"

    ok, note = _screenshot_with_navigation_retry(page, target, settle_ms=0)

    assert ok is False
    assert page.calls == [True, False]
    assert not target.exists()
    assert "full-page screenshot failed" in note
    assert "viewport screenshot failed" in note


def test_source_cache_accepts_product_images_without_screenshot(tmp_path):
    source_url = "https://www.amazon.com/dp/B0C3BC4QG2"
    cache_dir = tmp_path / "cache"
    slot = cache_dir / _source_cache_key(source_url)
    slot.mkdir(parents=True)
    write_source_snapshot(
        SourceSnapshot(
            requested_url=source_url,
            final_url=source_url,
            title="Product",
            captured_at="2026-08-14T00:00:00Z",
            visible_text="usable product evidence",
        ),
        slot / "source-snapshot.json",
    )
    image_dir = slot / "product-images"
    image_dir.mkdir()
    (image_dir / "source-image-01.jpg").write_bytes(b"image-bytes")

    captured = _cached_capture(
        source_url,
        output_dir=tmp_path / "output",
        cache_dir=cache_dir,
        cache_ttl_seconds=3600,
    )

    assert captured is not None
    assert captured.cache_hit is True
    assert captured.snapshot_path.is_file()
    assert not captured.screenshot_path.exists()
    assert len(captured.product_image_paths) == 1
    assert captured.product_image_paths[0].read_bytes() == b"image-bytes"
