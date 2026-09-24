from __future__ import annotations

import inspect

import makro_preview_listing
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


def test_full_step3_keeps_decision_evidence_and_upload_images_separate():
    args = build_parser().parse_args(
        [
            "--qa",
            "qa.xlsx",
            "--decision-packet",
            "ai-decisions.json",
            "--live-schema",
            "live-schema.json",
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
    assert args.decision_packet == "ai-decisions.json"
    assert args.live_schema == "live-schema.json"
    assert args.image == ["evidence.png"]
    assert args.upload_image == ["listing.png"]


def test_execution_main_rebinds_decision_and_checks_schema_before_any_fill():
    source = inspect.getsource(makro_preview_listing.main)
    rebind = "load_ai_decision_packet("
    schema_gate = "assert_live_schema_matches(planned_live_fields, semantic_fields)"
    fill = "_fill_one_section("
    assert rebind in source
    assert schema_gate in source
    assert source.index(rebind) < source.index(schema_gate) < source.index(fill)
    assert "build_live_fill_plan(\n            decision_packet" in source


def test_single_section_mode_also_requires_bound_decision_and_never_authorizes_save():
    args = build_parser().parse_args(
        [
            "--qa",
            "qa.xlsx",
            "--decision-packet",
            "ai-decisions.json",
            "--live-schema",
            "live-schema.json",
            "--expected-vertical",
            "vehicle_camera_system",
            "--section",
            "Product Description",
        ]
    )

    assert args.section == "Product Description"
    assert args.all_step3 is False
    assert args.allow_section_save is False


def test_executor_cli_no_longer_exposes_alias_or_ai_confidence_knobs():
    parser = build_parser()
    options = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--decision-packet" in options
    assert "--live-schema" in options
    assert "--alias-config" not in options
    assert "--auto-fill-min-confidence" not in options
    assert "--ai-auto-fill-min-confidence" not in options
    assert "--evidence-packet" not in options
