from __future__ import annotations

from makro_preview_listing import _has_existing_value


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
