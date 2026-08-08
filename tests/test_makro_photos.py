from __future__ import annotations

from pathlib import Path

from app.makro.photos import parse_completion_counter, upload_product_photos


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
