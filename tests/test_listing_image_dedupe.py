from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.listing_images import select_listing_images


def _base_product_image() -> Image.Image:
    image = Image.new("RGB", (800, 800), (248, 248, 248))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((250, 110, 550, 690), radius=48, fill=(32, 58, 92))
    draw.ellipse((310, 170, 490, 350), fill=(215, 225, 235))
    draw.rectangle((350, 400, 450, 620), fill=(78, 118, 158))
    return image


def test_listing_gate_collapses_same_visual_across_resize_and_jpeg_encoding(tmp_path: Path) -> None:
    original = _base_product_image()
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    different = tmp_path / "different.jpg"

    original.save(first, format="JPEG", quality=95)
    original.resize((640, 640)).save(second, format="JPEG", quality=72)

    other = Image.new("RGB", (800, 800), (248, 248, 248))
    draw = ImageDraw.Draw(other)
    draw.polygon([(120, 680), (400, 100), (680, 680)], fill=(130, 62, 82))
    other.save(different, format="JPEG", quality=90)

    selection = select_listing_images([first, second, different])

    assert selection.selected == (first.resolve(), different.resolve())
    assert selection.assessments[0].eligible is True
    assert selection.assessments[1].eligible is False
    assert "duplicate_visual" in selection.assessments[1].reasons
    assert selection.assessments[2].eligible is True


def test_listing_gate_keeps_distinct_product_views(tmp_path: Path) -> None:
    front = _base_product_image()
    side = Image.new("RGB", (800, 800), (248, 248, 248))
    draw = ImageDraw.Draw(side)
    draw.rounded_rectangle((120, 260, 680, 540), radius=48, fill=(32, 58, 92))
    draw.ellipse((500, 310, 620, 430), fill=(215, 225, 235))

    front_path = tmp_path / "front.jpg"
    side_path = tmp_path / "side.jpg"
    front.save(front_path, format="JPEG", quality=92)
    side.save(side_path, format="JPEG", quality=92)

    selection = select_listing_images([front_path, side_path])

    assert selection.selected == (front_path.resolve(), side_path.resolve())
