from __future__ import annotations

from pathlib import Path

from app.makro.photos import (
    _stage_accepted,
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


class FakeWaitPage:
    def __init__(self):
        self.waits = []

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


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
    assert page.waits == [250, 250]
