from __future__ import annotations

import inspect

import makro_visual_fill_all


def test_helper_search_is_not_a_listing_attribute():
    field_item = {
        "attribute_key": "Search for SKU ID",
        "label": "Search for SKU ID",
        "controls": [
            {
                "placeholder": "Search for SKU ID",
                "context_text": "Enter the SKU ID you want to copy attribute values from:",
            }
        ],
    }
    assert makro_visual_fill_all._is_non_listing_helper(field_item) is True


def test_real_listing_attribute_is_not_filtered():
    field_item = {
        "attribute_key": "sku_id",
        "label": "SKU ID",
        "controls": [
            {
                "id": "sku_id",
                "name": "sku_id_0_value",
                "placeholder": "",
                "context_text": "SKU ID cannot be empty.",
            }
        ],
    }
    assert makro_visual_fill_all._is_non_listing_helper(field_item) is False


def test_cli_does_not_hardcode_field_totals_or_cleanup_success_path():
    source = inspect.getsource(makro_visual_fill_all)
    assert "74/74" not in source
    assert "78/78" not in source
    assert "semantic_count == advertised" not in source
    assert "cleanup_all_visual_hold_sections" not in source
    assert "cleanup_visual_hold_section" not in source
    assert 'get_by_text("Save"' not in source
    assert 'get_by_text("Cancel"' not in source
    assert "browser.close()" not in source
    assert "context.close()" not in source
    assert "harness.detach()" in source


def test_vertical_is_required():
    parser = makro_visual_fill_all.build_parser()
    args = parser.parse_args(["--expected-vertical", "vehicle_camera_system"])
    assert args.expected_vertical == "vehicle_camera_system"
