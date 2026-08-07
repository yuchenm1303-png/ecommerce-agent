from __future__ import annotations

import inspect

import makro_listing_visual_hold
from app.makro.direct_visual_hold import is_listing_attribute_field


def test_helper_search_box_is_not_a_listing_attribute():
    helper = {
        "controls": [
            {
                "id": "",
                "name": "",
                "placeholder": "Search for SKU ID",
                "label": "Search for SKU ID",
            }
        ]
    }
    assert is_listing_attribute_field(helper) is False


def test_real_attribute_id_is_included():
    field = {"controls": [{"id": "shipping_days", "name": "shipping_days_0_value"}]}
    assert is_listing_attribute_field(field) is True


def test_real_indexed_attribute_name_is_included_without_id():
    field = {"controls": [{"id": "", "name": "depth_0_qualifier"}]}
    assert is_listing_attribute_field(field) is True


def test_cli_has_no_fixed_field_total_and_no_cleanup_action():
    source = inspect.getsource(makro_listing_visual_hold.main)
    assert "74/74" not in source
    assert "78/78" not in source
    assert 'get_by_text("Save"' not in source
    assert 'get_by_text("Send to QC"' not in source
    assert "cancel_section(" not in source
    assert "cleanup_visual_hold" not in source


def test_cli_requires_expected_vertical():
    args = makro_listing_visual_hold.build_parser().parse_args(
        ["--expected-vertical", "vehicle_camera_system"]
    )
    assert args.expected_vertical == "vehicle_camera_system"
