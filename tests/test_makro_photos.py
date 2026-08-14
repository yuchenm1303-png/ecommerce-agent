from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.makro.photos import (
    _normalize_for_makro_upload,
    _slot_is_empty,
    _stage_accepted,
    _wait_for_target_slot_completion,
    parse_completion_counter,
    upload_product_photos,
    verify_persisted_photo_count,
)


def test_parse_product_photo_completion_counter():
    assert parse_completion_counter("Product Photos (0/5)") == (0, 5)
    assert parse_completion_counter("Product Photos ( 3 / 5 )") == (3, 5)
    assert parse_completion_counter("Product Photos") is None


def test_photo_upload_rejects_missing_file_before_touching_page(tmp_path: Path):
    missing = tmp_path / "missing.png"

    result = upload_product_photos(object(), [missing])

    assert result.status == "invalid_input"
    assert result.attempted == 0
    assert result.staged == 0
    assert str(missing) in result.detail


def test_photo_upload_with_no_explicit_files_is_a_noop():
    result = upload_product_photos(object(), [])

    assert result.status == "skipped"
    assert result.attempted == 0
    assert result.staged == 0


def test_file_input_files_alone_do_not_count_as_makro_acceptance():
    state = {
        "file_inputs": [{"files": 1}],
        "visible_image_count": 0,
        "visible_image_sources": [],
        "completion_count": 0,
    }
    assert not _stage_accepted(
        state,
        before_images=0,
        before_sources=set(),
        before_completion=0,
    )


def test_new_preview_source_counts_as_makro_acceptance_even_if_img_count_is_same():
    state = {
        "file_inputs": [{"files": 1}],
        "visible_image_count": 1,
        "visible_image_sources": ["blob:new-product-photo"],
        "completion_count": 0,
    }
    assert _stage_accepted(
        state,
        before_images=1,
        before_sources={"/static/photo-placeholder.svg"},
        before_completion=0,
    )


def test_counter_growth_counts_as_makro_acceptance():
    state = {
        "file_inputs": [{"files": 0}],
        "visible_image_count": 0,
        "visible_image_sources": [],
        "completion_count": 1,
    }
    assert _stage_accepted(
        state,
        before_images=0,
        before_sources=set(),
        before_completion=0,
    )


def test_stale_plus_does_not_make_real_preview_slot_empty():
    assert _slot_is_empty(
        {
            "has_plus": True,
            "has_check": False,
            "image_sources": ["/static/photo-placeholder.svg"],
        }
    )
    assert not _slot_is_empty(
        {
            "has_plus": True,
            "has_check": False,
            "image_sources": ["blob:accepted-product-photo"],
        }
    )


def test_photo_normalization_creates_rgb_baseline_jpeg(tmp_path: Path):
    source = tmp_path / "transparent.png"
    Image.new("RGBA", (32, 16), (255, 0, 0, 128)).save(source)

    upload, meta = _normalize_for_makro_upload(source, tmp_path / "normalized")

    assert upload.suffix == ".jpg"
    assert meta["source_format"] == "PNG"
    assert meta["upload_format"] == "JPEG"
    with Image.open(upload) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (32, 16)


class FakeWaitPage:
    def __init__(self):
        self.waits = []

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


def test_target_acceptance_does_not_wait_for_stale_uploading_label(monkeypatch):
    page = FakeWaitPage()
    state = {
        "empty_slot_ids": ["thumbnail_0"],
        "visible_image_count": 1,
        "visible_image_sources": ["blob:new-product-photo"],
        "completion_count": 0,
        "capacity": 5,
        "add_image_tile_count": 5,
        "uploading": True,
        "slots": [
            {
                "id": "thumbnail_0",
                "has_plus": True,
                "has_check": False,
                "image_sources": ["blob:new-product-photo"],
            }
        ],
    }
    monkeypatch.setattr("app.makro.photos.find_section", lambda *_args: {"path": "#photos"})
    monkeypatch.setattr("app.makro.photos._photo_state", lambda *_args: state)

    settled = _wait_for_target_slot_completion(
        page,
        "#photos",
        "thumbnail_0",
        before_state={
            "visible_image_count": 0,
            "visible_image_sources": [],
            "completion_count": 0,
            "add_image_tile_count": 5,
        },
        accepted_stability_ms=0,
    )

    assert settled["acceptance_signal"] == "new_preview_source"
    assert settled["uploading_seen"] is True
    assert page.waits == []


def test_persisted_photo_count_polls_until_counter_updates(monkeypatch):
    page = FakeWaitPage()
    counts = iter([0, 0, 1])

    def fake_inspect(_page):
        return {"completion_count": next(counts)}

    monkeypatch.setattr("app.makro.photos.inspect_product_photos", fake_inspect)

    result = verify_persisted_photo_count(
        page,
        initial_count=0,
        expected_added=1,
        timeout_ms=5_000,
    )

    assert result["status"] == "persisted_verified"
    assert result["final_count"] == 1
    assert page.waits == [150, 150]
