from pathlib import Path

from app.makro import execution
from app.makro.execution import _photo_upload_budget
from makro_preview_listing import _completion_summary


class _FullGalleryAdapter:
    def find_section(self, _title: str):
        return {"has_edit": True, "path": "#photos", "title": "Product Photos (5/5)"}


def test_photo_upload_budget_uses_only_remaining_counter_capacity() -> None:
    budget = _photo_upload_budget(
        requested=5,
        initial_count=3,
        capacity=5,
        visible_empty_slots=5,
    )

    assert budget["available_slots"] == 2
    assert budget["upload_count"] == 2
    assert budget["omitted_count"] == 3


def test_photo_upload_budget_falls_back_to_visible_slots_without_counter() -> None:
    budget = _photo_upload_budget(
        requested=5,
        initial_count=1,
        capacity=None,
        visible_empty_slots=2,
    )

    assert budget["available_slots"] == 2
    assert budget["upload_count"] == 2
    assert budget["omitted_count"] == 3


def test_full_gallery_is_persisted_but_request_is_explicitly_capacity_limited(
    monkeypatch, tmp_path: Path
) -> None:
    omitted: list[Path] = []
    for index in range(5):
        path = tmp_path / f"image-{index}.jpg"
        path.write_bytes(b"fixture")
        omitted.append(path)
    monkeypatch.setattr(
        execution,
        "_fresh_photo_state",
        lambda _adapter: {"completion_count": 5, "capacity": 5, "add_image_tile_count": 0},
    )
    monkeypatch.setattr(execution, "_cancel_open_photo_transaction", lambda _adapter: True)

    report = execution.run_photos(
        _FullGalleryAdapter(),
        [str(path) for path in omitted],
        allow_save=True,
        upload_timeout_ms=1_000,
        run_dir=tmp_path,
    )

    assert report["status"] == "persisted_verified"
    assert report["request_status"] == "skipped_no_capacity"
    assert report["request_complete"] is False
    assert report["capacity_limited"] is True
    assert report["omitted_count"] == 5
    assert report["omitted_due_capacity"] == [str(path.resolve()) for path in omitted]
    assert report["listing_photo_requirement_satisfied"] is True
    assert report["persistence"]["final_count"] == 5
    assert report["attempted"] == 0
    assert report["save_attempted"] is False


def test_completion_does_not_fail_only_because_gallery_is_full() -> None:
    sections = [
        {
            "section": "Price, Stock and Shipping Information",
            "status": "persisted_verified",
            "review_candidates_persisted": 0,
        },
        {
            "section": "Product Description",
            "status": "persisted_verified",
            "review_candidates_persisted": 0,
        },
        {
            "section": "Additional Description",
            "status": "persisted_verified",
            "review_candidates_persisted": 0,
        },
    ]
    photo_report = {
        "status": "persisted_verified",
        "request_status": "skipped_no_capacity",
        "request_complete": False,
        "capacity_limited": True,
        "requested": 5,
        "omitted_count": 5,
        "persistence": {
            "status": "persisted_verified",
            "initial_count": 5,
            "final_count": 5,
            "expected_added": 0,
        },
    }

    completion = _completion_summary(
        sections,
        photo_report,
        {"required_blocked": 0},
    )

    assert completion["photos_persisted"] is True
    assert completion["draft_persisted_complete"] is True
    assert completion["autofill_safe_complete"] is True


def test_invalid_capacity_state_fails_closed() -> None:
    try:
        _photo_upload_budget(
            requested=1,
            initial_count=6,
            capacity=5,
            visible_empty_slots=0,
        )
    except ValueError as exc:
        assert "invalid photo capacity state" in str(exc)
    else:
        raise AssertionError("invalid counter state must fail closed")
