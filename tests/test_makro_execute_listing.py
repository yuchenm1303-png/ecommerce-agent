from __future__ import annotations

import inspect

import pytest

import makro_execute_listing


PRODUCT_URL = "https://detail.1688.com/offer/850845635717.html"


def _base_args():
    return [
        "--decision-packet", "ai-decisions.json",
        "--live-schema", "live-schema.json",
        "--product-url", PRODUCT_URL,
        "--supplier-snapshot", "source-snapshot.json",
        "--image", "source-page.png",
        "--expected-vertical", "vehicle_camera_system",
    ]


def test_direct_executor_cli_has_no_old_product_inputs():
    parser = makro_execute_listing.build_parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert "--product-url" in options
    assert "--supplier-snapshot" in options
    assert "--image" in options
    assert "--decision-packet" in options
    assert "--sku" not in options
    assert "--qa" not in options
    assert "--expected-model" not in options
    assert "--expected-brand" not in options
    assert "--product-table" not in options
    assert "--facts-json" not in options


def test_direct_executor_requires_exact_resolver_source_files():
    parser = makro_execute_listing.build_parser()
    args = parser.parse_args(
        [
            "--decision-packet", "ai-decisions.json",
            "--live-schema", "live-schema.json",
            "--product-url", PRODUCT_URL,
            "--expected-vertical", "vehicle_camera_system",
            "--section", "Product Description",
        ]
    )
    with pytest.raises(SystemExit, match="source-snapshot.json"):
        makro_execute_listing._validate_args(args)


def test_direct_executor_section_preview_never_authorizes_save():
    parser = makro_execute_listing.build_parser()
    args = parser.parse_args(_base_args() + ["--section", "Product Description"])
    makro_execute_listing._validate_args(args)
    assert args.section == "Product Description"
    assert args.all_step3 is False
    assert args.allow_section_save is False


def test_direct_executor_all_step3_requires_explicit_save_authorization():
    parser = makro_execute_listing.build_parser()
    args = parser.parse_args(_base_args() + ["--all-step3"])
    with pytest.raises(SystemExit, match="--allow-section-save"):
        makro_execute_listing._validate_args(args)


def test_direct_executor_rebinds_before_any_fill_and_has_no_semantic_context():
    source = inspect.getsource(makro_execute_listing.main)
    assert "load_ai_decision_packet(" in source
    assert "assert_live_schema_matches(planned_live_fields, semantic_fields)" in source
    assert "generated_business_bundle(args.product_url)" in source
    assert "build_ai_product_context" not in source
    assert "ResolutionInputSpec" not in source
    assert "load_question_catalog" not in source
    assert source.index("load_ai_decision_packet(") < source.index("_fill_one_section(")
