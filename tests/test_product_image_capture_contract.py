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


def test_image_sources_are_merged_before_ai_instead_of_program_tier_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured_urls: list[str] = []

    def fake_download(_context, urls: list[str], output_dir: Path, *, max_images: int = 32):
        captured_urls.extend(urls)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for index, _url in enumerate(urls[:max_images], start=1):
            path = output_dir / f"source-image-{index:02d}.jpg"
            Image.new("RGB", (300 + index, 300), (20 * index, 40, 60)).save(path)
            paths.append(path)
        return tuple(paths)

    monkeypatch.setattr("app.source_capture_engine._download_page_images", fake_download)
    source, urls, paths = _select_product_image_tier(
        None,
        [
            ("product_structured_gallery", ["gallery-a", "shared"]),
            ("visible_dom", ["shared", "visible-b"]),
            ("detail_document", ["detail-c"]),
        ],
        tmp_path / "product-images",
    )

    assert source == "merged_candidates"
    assert urls == ["gallery-a", "shared", "visible-b", "detail-c"]
    assert captured_urls == urls
    assert len(paths) == 4
