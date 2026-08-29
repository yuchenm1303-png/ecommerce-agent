from __future__ import annotations

import inspect

import pytest

import makro_plan_listing
from app.business_fields import generate_listing_sku


PRODUCT_URL = "https://detail.1688.com/offer/850845635717.html"


def _plan_args():
    return [
        "--decision-packet",
        "ai-decisions.json",
        "--live-schema",
        "live-schema.json",
        "--product-url",
        PRODUCT_URL,
        "--expected-vertical",
        "vehicle_camera_system",
    ]


def test_planner_has_explicit_live_schema_scan_mode_without_product_inputs():
    parser = makro_plan_listing.build_parser()
    args = parser.parse_args(
        [
            "--scan-live-schema",
            "--expected-vertical",
            "vehicle_camera_system",
        ]
    )
    makro_plan_listing._validate_mode(args)
    assert args.scan_live_schema is True
    assert args.decision_packet is None
    assert args.live_schema is None
    assert args.product_url is None


def test_final_plan_mode_requires_live_schema_and_product_url():
    parser = makro_plan_listing.build_parser()
    args = parser.parse_args(_plan_args())
    makro_plan_listing._validate_mode(args)
    assert args.decision_packet == "ai-decisions.json"
    assert args.live_schema == "live-schema.json"
    assert args.product_url == PRODUCT_URL
    assert args.expected_vertical == "vehicle_camera_system"

    missing_url = parser.parse_args(
        [
            "--decision-packet",
            "ai-decisions.json",
            "--live-schema",
            "live-schema.json",
            "--expected-vertical",
            "vehicle_camera_system",
        ]
    )
    with pytest.raises(SystemExit, match="--product-url"):
        makro_plan_listing._validate_mode(missing_url)

    missing_schema = parser.parse_args(
        [
            "--decision-packet",
            "ai-decisions.json",
            "--product-url",
            PRODUCT_URL,
            "--expected-vertical",
            "vehicle_camera_system",
        ]
    )
    with pytest.raises(SystemExit, match="--live-schema"):
        makro_plan_listing._validate_mode(missing_schema)


def test_live_planner_accepts_exact_captured_source_paths_for_strict_rebind():
    parser = makro_plan_listing.build_parser()
    args = parser.parse_args(
        _plan_args()
        + [
            "--supplier-snapshot",
            "supplier.json",
            "--official-snapshot",
            "official.json",
            "--image",
            "product.png",
        ]
    )
    assert args.supplier_snapshot == ["supplier.json"]
    assert args.official_snapshot == ["official.json"]
    assert args.image == ["product.png"]
    assert generate_listing_sku(args.product_url).isdigit()


def test_planner_has_no_manual_sku_qa_or_legacy_controls():
    parser = makro_plan_listing.build_parser()
    options = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--scan-live-schema" in options
    assert "--decision-packet" in options
    assert "--product-url" in options
    assert "--sku" not in options
    assert "--qa" not in options
    assert "--alias-config" not in options
    assert "--auto-fill-min-confidence" not in options
    assert "--ai-auto-fill-min-confidence" not in options
    assert "--evidence-packet" not in options


def test_final_plan_is_pure_and_never_attaches_makro_browser():
    source = inspect.getsource(makro_plan_listing._plan_from_captured_schema)
    assert "sync_playwright" not in source
    assert "EdgeHarness" not in source
    assert "connect_over_cdp" not in source
    assert "MakroDomainAdapter" not in source
    assert "load_ai_decision_packet(" in source
    assert "build_live_fill_plan(" in source
    assert "generated_sku = generate_listing_sku(str(args.product_url))" in source
    assert '"captured_parent_schema_no_browser_reattach"' in source


def test_browser_attach_exists_only_in_explicit_scan_mode():
    source = inspect.getsource(makro_plan_listing._scan_live_schema)
    assert "sync_playwright()" in source
    assert "EdgeHarness(" in source
    assert "_scan_live_fields(" in source

    main_source = inspect.getsource(makro_plan_listing.main)
    assert "if args.scan_live_schema:" in main_source
    assert "return _scan_live_schema(args)" in main_source
    assert "return _plan_from_captured_schema(args)" in main_source


def test_live_planner_contains_no_browser_fill_or_save_path():
    combined = (
        inspect.getsource(makro_plan_listing._scan_live_schema)
        + inspect.getsource(makro_plan_listing._plan_from_captured_schema)
    )
    assert "fill_resolved_field(" not in combined
    assert "exercise_live_field(" not in combined
    assert 'get_by_text("Save"' not in combined
    assert 'get_by_text("Send to QC"' not in combined
    assert "save_section(" not in combined
    assert "write_live_schema(" in combined
    assert "load_ai_decision_packet(" in combined
    assert "sku=generated_sku" in combined


def test_live_planner_blocks_unsaved_expanded_section():
    source = inspect.getsource(makro_plan_listing._assert_no_unsaved_section)
    assert "has_edit" in source
    assert "planner 已停止" in source
