from __future__ import annotations

import inspect

import makro_plan_listing


def _base_args():
    return [
        "--qa",
        "qa.xlsx",
        "--decision-packet",
        "ai-decisions.json",
        "--live-schema",
        "live-schema.json",
        "--expected-vertical",
        "vehicle_camera_system",
    ]


def test_live_planner_requires_decision_schema_and_expected_vertical():
    parser = makro_plan_listing.build_parser()
    args = parser.parse_args(_base_args())
    assert args.decision_packet == "ai-decisions.json"
    assert args.live_schema == "live-schema.json"
    assert args.expected_vertical == "vehicle_camera_system"


def test_live_planner_rebuilds_same_product_source_pack_for_strict_rebind():
    parser = makro_plan_listing.build_parser()
    args = parser.parse_args(
        _base_args()
        + [
            "--supplier-snapshot",
            "supplier.json",
            "--official-snapshot",
            "official.json",
            "--image",
            "product.png",
        ]
    )
    spec = makro_plan_listing._input_spec(args)
    assert spec.supplier_snapshots == ("supplier.json",)
    assert spec.official_snapshots == ("official.json",)
    assert spec.image_paths == ("product.png",)


def test_planner_has_no_alias_confidence_or_legacy_evidence_packet_controls():
    parser = makro_plan_listing.build_parser()
    options = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--decision-packet" in options
    assert "--alias-config" not in options
    assert "--auto-fill-min-confidence" not in options
    assert "--ai-auto-fill-min-confidence" not in options
    assert "--evidence-packet" not in options


def test_live_planner_contains_no_browser_fill_or_save_path():
    source = inspect.getsource(makro_plan_listing.main)
    assert "fill_resolved_field(" not in source
    assert "exercise_live_field(" not in source
    assert 'get_by_text("Save"' not in source
    assert 'get_by_text("Send to QC"' not in source
    assert "save_section(" not in source
    assert "write_live_schema(" in source
    assert "load_ai_decision_packet(" in source
    assert "assert_live_schema_matches(" in source
    assert "build_live_fill_plan(\n            decision_packet" in source


def test_live_planner_blocks_unsaved_expanded_section():
    source = inspect.getsource(makro_plan_listing._assert_no_unsaved_section)
    assert "has_edit" in source
    assert "planner 已停止" in source
