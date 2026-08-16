from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PHOTO_OWNERSHIP = (ROOT / "gui" / "listing_photo_ownership.py").read_text(encoding="utf-8")
WORKFLOW = (ROOT / "gui" / "workflow_console_window.py").read_text(encoding="utf-8")
ENTRY = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_batch_manual_photos_are_job_owned_and_override_supplier_fallback() -> None:
    assert '"listing-photo-intent.json"' in PHOTO_OWNERSHIP
    assert '"manual_listing_images"' in PHOTO_OWNERSHIP
    assert "_manual_images_by_job_id" in PHOTO_OWNERSHIP
    assert "_match_rows_to_urls" in PHOTO_OWNERSHIP
    assert "defaultdict, deque" in PHOTO_OWNERSHIP
    assert "_replace_upload_image_args" in PHOTO_OWNERSHIP
    assert 'listing_photos=MANUAL' in PHOTO_OWNERSHIP
    assert 'supplier_fallback=disabled' in PHOTO_OWNERSHIP


def test_batch_manual_photos_refresh_again_immediately_before_execute() -> None:
    assert "original = self.controller.start_execution" in PHOTO_OWNERSHIP
    assert "matched = self._match_rows_to_urls(job.product_url for job in batch.jobs)" in PHOTO_OWNERSHIP
    assert "self._set_job_images(job, images)" in PHOTO_OWNERSHIP


def test_photo_ownership_is_installed_after_existing_batch_layers() -> None:
    assert "from gui.listing_photo_ownership import install_listing_photo_ownership" in ENTRY
    assert ENTRY.index("install_batch_product_files(window)") < ENTRY.index(
        "install_listing_photo_ownership(window)"
    )
    assert ENTRY.index("install_listing_offer_hardening(window)") < ENTRY.index(
        "install_listing_photo_ownership(window)"
    )


def test_single_manual_photos_survive_same_product_reprepare_only() -> None:
    assert "preserve_manual = bool(" in WORKFLOW
    assert "previous_url.casefold() == current_url.casefold()" in WORKFLOW
    assert "self._selected_upload_images = manual_images" in WORKFLOW
    assert 'self.real_image_count.setText(f"MANUAL {len(manual_images)}")' in WORKFLOW


def test_single_auto_fallback_never_becomes_persistent_manual_state() -> None:
    assert "temporary_auto = False" in WORKFLOW
    assert "temporary_auto = True" in WORKFLOW
    assert "finally:" in WORKFLOW
    assert "if temporary_auto:" in WORKFLOW
    assert "self._selected_upload_images = manual_images" in WORKFLOW
