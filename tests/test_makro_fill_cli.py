from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

import makro_fill


def test_makro_fill_cli_imports_and_defaults_to_dry_run():
    args = makro_fill.build_parser().parse_args(["--product", "fixture.xlsx"])

    assert args.dry_run is True
    assert args.source_format == "auto"
    assert args.expected_vertical is None
    assert args.cdp_port == 9222


def test_section_title_count_is_not_part_of_identity():
    assert makro_fill._base_section_title("Product Description (14/14)") == "Product Description"
    assert makro_fill._base_section_title("Additional Description (Optional) (0/46)") == "Additional Description (Optional)"


def test_target_section_uses_resolved_answers_only():
    sections = [
        {"title": "Price, Stock and Shipping Information (0/14)"},
        {"title": "Product Description (0/14)"},
    ]
    resolutions = [
        {"status": "missing", "section_heading": "Price, Stock and Shipping Information (0/14)"},
        {"status": "resolved", "section_heading": "Product Description (0/14)"},
    ]

    assert makro_fill._select_target_section(sections, resolutions, None) == "Product Description"


def test_expected_vertical_guard_accepts_matching_listing():
    page = SimpleNamespace(
        url="https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=vehicle_camera_system"
    )

    makro_fill._assert_expected_vertical(page, "vehicle_camera_system")


def test_expected_vertical_guard_blocks_wrong_listing_before_fill():
    page = SimpleNamespace(
        url="https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=sports_action_camera"
    )

    with pytest.raises(RuntimeError, match="vertical"):
        makro_fill._assert_expected_vertical(page, "vehicle_camera_system")


def test_cli_has_no_save_submit_or_owned_browser_launch_action():
    source = inspect.getsource(makro_fill)

    assert "select_option" not in source  # field writes live in makro_dryrun, not ad-hoc CLI code
    assert 'get_by_text("Save"' not in source
    assert 'get_by_text("Send to QC"' not in source
    assert ".save(" not in source
    assert "launch_persistent_context" not in source
    # The executable code must not own/close the long-lived Edge session.
    executable_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "context.close()" not in executable_lines
    assert "browser.close()" not in executable_lines
