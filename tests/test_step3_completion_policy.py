from __future__ import annotations

from makro_preview_listing import _completion_summary


def _persisted_sections() -> list[dict[str, object]]:
    return [
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
            "status": "no_candidates",
            "review_candidates_persisted": 0,
        },
    ]


def test_zero_validated_upload_images_do_not_invalidate_persisted_fields() -> None:
    completion = _completion_summary(
        _persisted_sections(),
        {
            "status": "skipped",
            "requested": 0,
            "persistence": {"final_count": 0},
        },
        {"required_blocked": 0},
    )

    assert completion["photos_requested"] is False
    assert completion["photos_persisted"] is False
    assert completion["photo_requirement_satisfied"] is True
    assert completion["draft_persisted_complete"] is True
    assert completion["autofill_safe_complete"] is True


def test_requested_photo_upload_still_must_persist() -> None:
    completion = _completion_summary(
        _persisted_sections(),
        {
            "status": "failed",
            "requested": 1,
            "persistence": {"final_count": 0},
        },
        {"required_blocked": 0},
    )

    assert completion["photos_requested"] is True
    assert completion["photos_persisted"] is False
    assert completion["photo_requirement_satisfied"] is False
    assert completion["draft_persisted_complete"] is False
