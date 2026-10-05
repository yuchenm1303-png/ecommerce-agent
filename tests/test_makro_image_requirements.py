from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.makro import domain as makro_domain
from app.makro.image_requirements import (
    inspect_listing_image,
    listing_image_cms_error,
    listing_image_coverage_error,
    listing_image_resolution_error,
)


def _save(path: Path, image: Image.Image) -> Path:
    image.save(path)
    return path


def test_rejects_makro_example_below_minimum_resolution(tmp_path: Path) -> None:
    path = _save(tmp_path / "160x348.png", Image.new("RGB", (160, 348), "black"))

    inspection = inspect_listing_image(path)

    assert inspection.meets_resolution is False
    assert listing_image_resolution_error(inspection) is not None
    assert listing_image_cms_error(inspection) is not None


def test_rejects_large_canvas_with_too_little_non_blank_content(tmp_path: Path) -> None:
    image = Image.new("RGB", (500, 500), "white")
    ImageDraw.Draw(image).rectangle((150, 120, 349, 379), fill="black")
    path = _save(tmp_path / "small-content.png", image)

    inspection = inspect_listing_image(path)

    assert inspection.meets_resolution is True
    assert (inspection.content_width, inspection.content_height) == (200, 260)
    assert inspection.meets_content_coverage is False
    assert listing_image_coverage_error(inspection) is not None
    assert listing_image_cms_error(inspection) is not None


def test_accepts_image_when_canvas_and_non_blank_content_meet_cms_thresholds(tmp_path: Path) -> None:
    image = Image.new("RGB", (500, 500), "white")
    ImageDraw.Draw(image).rectangle((100, 100, 399, 399), fill="black")
    path = _save(tmp_path / "valid.png", image)

    inspection = inspect_listing_image(path)

    assert inspection.meets_resolution is True
    assert (inspection.content_width, inspection.content_height) == (300, 300)
    assert inspection.meets_content_coverage is True
    assert inspection.meets_cms_requirements is True
    assert listing_image_cms_error(inspection) is None


def test_transparent_padding_does_not_count_as_product_coverage(tmp_path: Path) -> None:
    image = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((180, 180, 419, 419), fill=(20, 20, 20, 255))
    path = _save(tmp_path / "transparent.png", image)

    inspection = inspect_listing_image(path)

    assert (inspection.content_width, inspection.content_height) == (240, 240)
    assert inspection.meets_cms_requirements is True
    assert listing_image_cms_error(inspection) is None


def test_domain_skips_bad_candidate_and_continues_with_good_image(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bad = _save(tmp_path / "bad.png", Image.new("RGB", (160, 348), "black"))
    good_image = Image.new("RGB", (500, 500), "white")
    ImageDraw.Draw(good_image).rectangle((100, 100, 399, 399), fill="black")
    good = _save(tmp_path / "good.png", good_image)
    captured: dict[str, object] = {}

    def fake_upload_product_photos(page, image_paths, *, timeout_ms):
        captured["page"] = page
        captured["paths"] = list(image_paths)
        captured["timeout_ms"] = timeout_ms
        return makro_domain.PhotoUploadResult(
            status="staged",
            attempted=1,
            staged=1,
            detail="1/1 张可提交图片已事务确认；等待 Save。",
        )

    monkeypatch.setattr(makro_domain, "upload_product_photos", fake_upload_product_photos)
    page = object()
    adapter = makro_domain.MakroDomainAdapter(page)

    result = adapter.upload_product_photos([bad, good], timeout_ms=12_345)

    assert captured["page"] is page
    assert captured["paths"] == [good.resolve()]
    assert captured["timeout_ms"] == 12_345
    assert result.status == "staged"
    assert result.staged == 1
    assert result.items[0]["status"] == "rejected_pre_submit"
    assert result.items[0]["validation"] == "makro_cms_image_requirements"
    assert "160x348" in result.items[0]["detail"]
    assert "隔离 1 张不合格图片" in result.detail
