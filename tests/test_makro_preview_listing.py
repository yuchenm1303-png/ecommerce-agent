from __future__ import annotations

from makro_preview_listing import _has_existing_value, build_parser


def test_review_preview_detects_existing_text_value():
    field = {
        "controls": [
            {
                "value_recorded": True,
                "value": "M8",
            }
        ]
    }

    assert _has_existing_value(field) is True


def test_review_preview_treats_default_select_placeholder_as_empty():
    field = {
        "controls": [
            {
                "options": [
                    {"text": "Select One", "value": "", "selected": True},
                    {"text": "Yes", "value": "Yes", "selected": False},
                ]
            }
        ]
    }

    assert _has_existing_value(field) is False


def test_review_preview_detects_non_placeholder_selected_option():
    field = {
        "controls": [
            {
                "options": [
                    {"text": "Select One", "value": "", "selected": False},
                    {"text": "Yes", "value": "Yes", "selected": True},
                ]
            }
        ]
    }

    assert _has_existing_value(field) is True


def test_full_step3_mode_keeps_evidence_images_separate_from_upload_images():
    args = build_parser().parse_args(
        [
            "--qa",
            "qa.xlsx",
            "--expected-vertical",
            "vehicle_camera_system",
            "--all-step3",
            "--allow-section-save",
            "--image",
            "evidence.png",
            "--upload-image",
            "listing.png",
        ]
    )

    assert args.all_step3 is True
    assert args.allow_section_save is True
    assert args.section is None
    assert args.image == ["evidence.png"]
    assert args.upload_image == ["listing.png"]


def test_single_section_mode_does_not_implicitly_authorize_save():
    args = build_parser().parse_args(
        [
            "--qa",
            "qa.xlsx",
            "--expected-vertical",
            "vehicle_camera_system",
            "--section",
            "Product Description",
        ]
    )

    assert args.section == "Product Description"
    assert args.all_step3 is False
    assert args.allow_section_save is False
