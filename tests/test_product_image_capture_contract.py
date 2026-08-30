from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from app.source_capture_engine import (
    _download_page_images,
    _select_product_image_tier,
    _structured_product_image_urls,
)
from app.source_snapshot import SourceSnapshot


def _snapshot(json_ld: list[object]) -> SourceSnapshot:
    return SourceSnapshot(
        requested_url="https://shop.example/products/widget",
        final_url="https://shop.example/products/widget?variant=1",
        title="Widget",
        captured_at="2026-08-30T00:00:00+00:00",
        json_ld=json_ld,
    )


def test_structured_product_images_only_follow_product_jsonld() -> None:
    snapshot = _snapshot(
        [
            {
                "@graph": [
                    {
                        "@type": "Product",
                        "name": "Widget",
                        "image": [
                            "//cdn.example/widget-main.webp",
                            {"contentUrl": "/media/widget-side.jpg"},
                        ],
                    },
                    {
                        "@type": "Organization",
                        "image": "https://cdn.example/company-logo.png",
                    },
                ]
            }
        ]
    )

    assert _structured_product_image_urls(snapshot) == [
        "https://cdn.example/widget-main.webp",
        "https://shop.example/media/widget-side.jpg",
    ]


def test_download_rejects_non_image_payload_and_normalizes_real_pixels(
    tmp_path: Path,
    monkeypatch,
) -> None:
    invalid = b"<html>not an image</html>" + b"x" * 6000
    buffer = BytesIO()
    Image.new("RGB", (360, 360), (40, 80, 120)).save(buffer, format="BMP")
    valid = buffer.getvalue()

    class Response:
        def __init__(self, body: bytes, final_url: str) -> None:
            self.body = body
            self.final_url = final_url
            self.content_type = "application/octet-stream"

    def fake_fetch(url: str, **_kwargs):
        if url.endswith("bad.img"):
            return Response(invalid, url)
        return Response(valid, url)

    monkeypatch.setattr("app.source_capture_engine.fetch_public_resource", fake_fetch)
    paths = _download_page_images(
        None,
        ["https://cdn.example/bad.img", "https://cdn.example/good.bin"],
        tmp_path / "images",
    )

    assert len(paths) == 1
    assert paths[0].suffix == ".jpg"
    with Image.open(paths[0]) as opened:
        assert opened.format == "JPEG"
        assert opened.size == (360, 360)


def test_image_source_tier_falls_back_when_earlier_pixels_are_not_listing_viable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_download(_context, urls: list[str], output_dir: Path, *, max_images: int = 32):
        del max_images
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "source-image-01.jpg"
        size = (649, 45) if urls == ["strip"] else (679, 762)
        Image.new("RGB", size, (20, 40, 60)).save(path)
        return (path,)

    monkeypatch.setattr("app.source_capture_engine._download_page_images", fake_download)
    source, urls, paths = _select_product_image_tier(
        None,
        [
            ("product_structured_gallery", ["strip"]),
            ("visible_dom_fallback", ["product"]),
        ],
        tmp_path / "product-images",
    )

    assert source == "visible_dom_fallback"
    assert urls == ["product"]
    assert len(paths) == 1
    with Image.open(paths[0]) as opened:
        assert opened.size == (679, 762)
