from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.makro.photos import (
    _normalize_for_makro_upload,
    _open_photo_slot_upload_panel,
    _slot_is_empty,
    _stage_accepted,
    _target_slot_acceptance_signal,
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


def test_new_preview_source_counts_as_page_level_makro_acceptance():
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


def test_visible_plus_remains_dom_level_uploadable_state():
    assert _slot_is_empty(
        {
            "has_plus": True,
            "has_check": False,
            "image_sources": ["/static/photo-placeholder.svg"],
        }
    )
    # A blank Makro role may contain decorative images. DOM emptiness therefore
    # remains plus-driven; transaction acceptance handles before/after evidence.
    assert _slot_is_empty(
        {
            "has_plus": True,
            "has_check": False,
            "image_sources": ["/static/media/image.abc123.png"],
        }
    )
    assert not _slot_is_empty(
        {
            "has_plus": True,
            "has_check": True,
            "image_sources": ["blob:accepted-product-photo"],
        }
    )


def _state(
    *,
    plus: bool,
    check: bool,
    sources: list[str],
    completion: int = 0,
    uploading: bool = False,
):
    slot = {
        "id": "thumbnail_0",
        "has_plus": plus,
        "has_check": check,
        "image_sources": list(sources),
    }
    return {
        "empty_slot_ids": ["thumbnail_0"] if plus and not check else [],
        "visible_image_count": len(sources),
        "visible_image_sources": list(sources),
        "completion_count": completion,
        "capacity": 5,
        "add_image_tile_count": 1 if plus and not check else 0,
        "uploading": uploading,
        "slots": [slot],
    }


def test_preexisting_decorative_image_is_not_target_transaction_acceptance():
    before = _state(
        plus=True,
        check=False,
        sources=["/static/media/image.abc123.png"],
    )
    after = _state(
        plus=True,
        check=False,
        sources=["/static/media/image.abc123.png"],
    )

    signal, new_sources = _target_slot_acceptance_signal(before, after, "thumbnail_0")

    assert signal == ""
    assert new_sources == set()


def test_new_target_preview_is_acceptance_even_while_plus_is_stale():
    before = _state(
        plus=True,
        check=False,
        sources=["/static/media/image.abc123.png"],
    )
    after = _state(
        plus=True,
        check=False,
        sources=["/static/media/image.abc123.png", "blob:new-product-photo"],
    )

    signal, new_sources = _target_slot_acceptance_signal(before, after, "thumbnail_0")

    assert signal == "target_slot_new_preview"
    assert new_sources == {"blob:new-product-photo"}


def test_check_or_consumed_plus_is_strong_target_acceptance():
    before = _state(plus=True, check=False, sources=[])

    checked = _state(plus=True, check=True, sources=["blob:new-product-photo"])
    signal, _ = _target_slot_acceptance_signal(before, checked, "thumbnail_0")
    assert signal == "target_slot_check"

    consumed = _state(plus=False, check=False, sources=["blob:new-product-photo"])
    signal, _ = _target_slot_acceptance_signal(before, consumed, "thumbnail_0")
    assert signal == "target_slot_consumed"


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


class FakePostClickTimeoutSlot:
    def __init__(self):
        self.clicks = 0

    def click(self, *, timeout, force):
        assert force is True
        assert timeout > 0
        self.clicks += 1
        raise PlaywrightTimeoutError(
            "Timeout exceeded after click action done while waiting for scheduled navigations"
        )


def test_slot_open_uses_panel_postcondition_after_post_click_timeout(monkeypatch):
    page = FakeWaitPage()
    slot = FakePostClickTimeoutSlot()
    upload_button = object()
    monkeypatch.setattr("app.makro.photos.find_section", lambda *_args: {"path": "#live-photos"})
    monkeypatch.setattr(
        "app.makro.photos._wait_for_upload_photo_button",
        lambda _page, path, *, timeout_ms: upload_button if path == "#live-photos" else None,
    )

    live_path, actual_button = _open_photo_slot_upload_panel(
        page,
        "#old-photos",
        slot,
        "thumbnail_2",
        click_timeout_ms=1_500,
        panel_timeout_ms=2_000,
    )

    assert live_path == "#live-photos"
    assert actual_button is upload_button
    assert slot.clicks == 1


def test_slot_open_does_not_swallow_timeout_when_panel_never_opens(monkeypatch):
    page = FakeWaitPage()
    slot = FakePostClickTimeoutSlot()
    monkeypatch.setattr("app.makro.photos.find_section", lambda *_args: {"path": "#live-photos"})
    monkeypatch.setattr(
        "app.makro.photos._wait_for_upload_photo_button",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="Playwright 等待超时"):
        _open_photo_slot_upload_panel(
            page,
            "#old-photos",
            slot,
            "thumbnail_2",
            click_timeout_ms=1_500,
            panel_timeout_ms=2_000,
        )

    assert slot.clicks == 1


def test_target_completion_accepts_exact_slot_preview_with_stale_plus(monkeypatch):
    page = FakeWaitPage()
    before = _state(
        plus=True,
        check=False,
        sources=["/static/media/image.abc123.png"],
    )
    after = _state(
        plus=True,
        check=False,
        sources=["/static/media/image.abc123.png", "blob:new-product-photo"],
        uploading=False,
    )
    monkeypatch.setattr("app.makro.photos.find_section", lambda *_args: {"path": "#photos"})
    monkeypatch.setattr("app.makro.photos._photo_state", lambda *_args: after)

    settled = _wait_for_target_slot_completion(
        page,
        "#photos",
        "thumbnail_0",
        before_state=before,
        accepted_stability_ms=0,
    )

    assert settled["acceptance_signal"] == "target_slot_new_preview"
    assert settled["new_target_sources"] == ["blob:new-product-photo"]
    assert settled["target_slot_before"]["has_plus"] is True
    assert settled["target_slot_after"]["has_plus"] is True
    assert page.waits == []


def test_target_completion_still_accepts_exact_role_consumption(monkeypatch):
    page = FakeWaitPage()
    before = _state(plus=True, check=False, sources=[])
    after = _state(
        plus=False,
        check=True,
        sources=["blob:new-product-photo"],
        completion=1,
    )
    monkeypatch.setattr("app.makro.photos.find_section", lambda *_args: {"path": "#photos"})
    monkeypatch.setattr("app.makro.photos._photo_state", lambda *_args: after)

    settled = _wait_for_target_slot_completion(
        page,
        "#photos",
        "thumbnail_0",
        before_state=before,
        accepted_stability_ms=0,
    )

    assert settled["acceptance_signal"] == "target_slot_check"
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
