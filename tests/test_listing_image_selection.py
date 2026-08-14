from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.listing_images import (
    listing_images_from_resolver_outputs,
    select_listing_images,
    write_listing_image_selection,
)


ROOT = Path(__file__).resolve().parents[1]
RESOLVER = (ROOT / "makro_resolve_ai.py").read_text(encoding="utf-8")
WINDOW = (ROOT / "gui" / "workflow_console_window.py").read_text(encoding="utf-8")
BATCH = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")
REAL = (ROOT / "gui" / "real_execution.py").read_text(encoding="utf-8")


def _write_image(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> Path:
    Image.new("RGB", size, color).save(path)
    return path.resolve()


def test_pathological_strip_is_not_a_listing_photo(tmp_path: Path) -> None:
    strip = _write_image(tmp_path / "strip.png", (649, 45), (255, 255, 255))
    normal = _write_image(tmp_path / "normal.jpg", (679, 762), (20, 40, 60))

    selection = select_listing_images([strip, normal])

    assert selection.selected == (normal,)
    rejected = selection.assessments[0]
    assert rejected.eligible is False
    assert rejected.width == 649
    assert rejected.height == 45
    assert "short_edge<160" in rejected.reasons
    assert "pixel_area<80000" in rejected.reasons
    assert "aspect_ratio>4" in rejected.reasons


def test_normal_square_and_portrait_images_keep_source_order(tmp_path: Path) -> None:
    portrait = _write_image(tmp_path / "portrait.jpg", (679, 762), (10, 20, 30))
    square_a = _write_image(tmp_path / "square-a.jpg", (330, 330), (40, 50, 60))
    square_b = _write_image(tmp_path / "square-b.jpg", (330, 330), (70, 80, 90))

    selection = select_listing_images([portrait, square_a, square_b])

    assert selection.selected == (portrait, square_a, square_b)
    assert selection.rejected_count == 0


def test_exact_duplicate_content_is_not_repeated_in_listing_gallery(tmp_path: Path) -> None:
    first = _write_image(tmp_path / "first.jpg", (330, 330), (1, 2, 3))
    duplicate = tmp_path / "duplicate.jpg"
    duplicate.write_bytes(first.read_bytes())

    selection = select_listing_images([first, duplicate])

    assert selection.selected == (first,)
    assert selection.assessments[1].eligible is False
    assert selection.assessments[1].reasons == ("duplicate_content",)


def test_selection_report_preserves_rejection_reason(tmp_path: Path) -> None:
    strip = _write_image(tmp_path / "strip.png", (649, 45), (255, 255, 255))
    normal = _write_image(tmp_path / "normal.jpg", (330, 330), (10, 30, 50))
    target = tmp_path / "listing-image-selection.json"

    selection = select_listing_images([strip, normal])
    write_listing_image_selection(selection, target)
    text = target.read_text(encoding="utf-8")

    assert '"selected_count": 1' in text
    assert '"rejected_count": 1' in text
    assert "short_edge<160" in text
    assert str(normal) in text


def test_legacy_resolver_run_is_filtered_by_same_gate(tmp_path: Path) -> None:
    strip = _write_image(tmp_path / "source-image-01.png", (649, 45), (255, 255, 255))
    normal = _write_image(tmp_path / "source-image-02.jpg", (330, 330), (10, 20, 30))

    selected = listing_images_from_resolver_outputs(
        {"primary_source_product_images": [str(strip), str(normal)]}
    )

    assert selected == (normal,)


def test_explicit_curated_output_is_authoritative_and_never_falls_back(tmp_path: Path) -> None:
    raw = _write_image(tmp_path / "raw.jpg", (330, 330), (10, 20, 30))

    selected = listing_images_from_resolver_outputs(
        {
            "primary_source_product_images": [str(raw)],
            "primary_source_listing_images": [],
        }
    )

    assert selected == ()


def test_resolver_keeps_raw_ai_evidence_separate_from_listing_images() -> None:
    assert "select_listing_images(product_images)" in RESOLVER
    assert '"primary_source_product_images"' in RESOLVER
    assert '"primary_source_listing_images"' in RESOLVER
    assert '"primary_source_listing_image_selection"' in RESOLVER
    assert "image_paths = [*(product_images or [str(captured.screenshot_path)]), *extra_images]" in RESOLVER
    assert 'outputs.get("primary_source_product_images")' in REAL


def test_single_and_batch_share_the_canonical_listing_image_gate() -> None:
    assert "listing_images_from_resolver_outputs(outputs)" in WINDOW
    assert "listing_images_from_resolver_outputs(outputs)" in BATCH
    assert 'outputs.get("primary_source_product_images") or []' not in BATCH
