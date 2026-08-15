from __future__ import annotations

from app.makro.fields import _SCAN_SCRIPT, _merge_semantic_field
from app.makro.locators import ADD_VALUE_CONTROL_JS


def test_schema_scanner_and_executor_share_exact_add_value_detector():
    assert ADD_VALUE_CONTROL_JS in _SCAN_SCRIPT
    assert '[role="button"]' in ADD_VALUE_CONTROL_JS
    assert '[onclick]' in ADD_VALUE_CONTROL_JS
    assert '[tabindex]:not([tabindex="-1"])' in ADD_VALUE_CONTROL_JS
    assert "data-testid" in ADD_VALUE_CONTROL_JS
    assert "actions.length !== 1" in ADD_VALUE_CONTROL_JS


def test_live_add_marker_makes_single_rendered_slot_repeatable():
    field = _merge_semantic_field(
        "keywords",
        [
            {
                "id": "keywords",
                "name": "keywords_0_value",
                "field_kind": "input",
                "label": "Keywords",
                "section_heading": "Additional Description",
                "required": False,
                "has_add_value_control": True,
                "options": [],
            }
        ],
    )

    assert field["has_add_value_control"] is True
    assert field["multi_value"] is True


def test_indexed_name_without_live_add_marker_stays_single_value():
    field = _merge_semantic_field(
        "processor",
        [
            {
                "id": "processor",
                "name": "processor_0_value",
                "field_kind": "input",
                "label": "Processor",
                "section_heading": "Additional Description",
                "required": False,
                "has_add_value_control": False,
                "options": [],
            }
        ],
    )

    assert field["has_add_value_control"] is False
    assert field["multi_value"] is False
