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


def test_mechanical_gate_keeps_near_duplicate_pixels_for_ai_judgment(tmp_path: Path) -> None:
    original = _base_product_image()
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    original.save(first, format="JPEG", quality=95)
    original.resize((620, 620)).save(second, format="JPEG", quality=72)

    selection = select_listing_images([first, second])

    assert selection.selected == (first.resolve(), second.resolve())
    assert selection.rejected_count == 0


def test_mechanical_gate_dedupes_only_exact_file_content(tmp_path: Path) -> None:
    image = _base_product_image()
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    image.save(first, format="JPEG", quality=90)
    second.write_bytes(first.read_bytes())

    selection = select_listing_images([first, second])

    assert selection.selected == (first.resolve(),)
    assert selection.assessments[1].reasons == ("duplicate_content",)
