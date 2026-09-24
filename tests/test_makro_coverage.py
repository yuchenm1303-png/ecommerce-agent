from __future__ import annotations

import inspect

import makro_coverage
from app.makro.coverage import (
    PASS,
    CoverageResult,
    _static_unit_suffix,
    choose_option,
    control_is_empty,
    field_shape,
    semantic_field_is_empty,
    summarize_results,
)


def _control(**overrides):
    base = {
        "field_kind": "input",
        "type": "text",
        "name": "field_0_value",
        "label": "Field",
        "context_text": "Field",
        "readonly": False,
        "disabled": False,
        "value_recorded": False,
        "options": [],
    }
    base.update(overrides)
    return base


def _field(control, **overrides):
    base = {
        "attribute_key": "field",
        "label": control.get("label", "Field"),
        "section_heading": "Additional Description (Optional) (0/46)",
        "subsection_heading": "GENERAL",
        "controls": [control],
    }
    base.update(overrides)
    return base


def test_native_select_placeholder_is_empty_and_real_option_is_chosen():
    control = _control(
        field_kind="select",
        type="select",
        value="Select One",
        value_recorded=True,
        options=[
            {"text": "Select One", "value": "Select One", "selected": True, "disabled": False},
            {"text": "Back", "value": "Back", "selected": False, "disabled": False},
            {"text": "Front", "value": "Front", "selected": False, "disabled": False},
        ],
    )
    assert control_is_empty(control) is True
    assert choose_option(control) == "Back"
    assert choose_option(control, avoid="Back") == "Front"


def test_existing_non_placeholder_value_is_not_treated_as_empty():
    control = _control(value="Existing", value_recorded=True)
    assert control_is_empty(control) is False
    assert semantic_field_is_empty(_field(control)) is False


def test_operating_temperature_style_static_unit_is_detected_without_hardcoded_unit_list():
    control = _control(
        label="Operating Temperature",
        context_text="Operating Temperature K",
    )
    assert _static_unit_suffix(control) == "K"
    assert field_shape(_field(control)) == "text+static-unit"


def test_plus_marker_is_not_misclassified_as_unit():
    control = _control(
        label="Ingress Protection Ratings",
        context_text="Ingress Protection Ratings +",
    )
    assert _static_unit_suffix(control) == ""
    assert field_shape(_field(control)) == "text"


def test_qualifier_changes_field_shape():
    value = _control(name="weight_0_value", label="Weight")
    qualifier = _control(
        field_kind="select",
        type="select",
        name="weight_0_qualifier",
        label="Weight",
        options=[
            {"text": "kg", "value": "kg", "selected": False, "disabled": False},
        ],
    )
    field = _field(value, controls=[value, qualifier])
    assert field_shape(field) == "text+qualifier"


def test_summary_counts_unsupported_or_fail_as_not_passed():
    results = [
        CoverageResult("AD", "", "a", "A", "text", PASS),
        CoverageResult("AD", "", "b", "B", "native-select", "unsupported"),
        CoverageResult("AD", "", "c", "C", "text", "skipped_existing"),
    ]
    summary = summarize_results(results)
    assert summary["empty_field_attempts"] == 2
    assert summary["passed"] == 1
    assert summary["failed_or_unsupported"] == 1
    assert summary["skipped_existing"] == 1
    assert summary["all_empty_passed"] is False


def test_cli_defaults_to_additional_description_and_requires_vertical():
    parser = makro_coverage.build_parser()
    args = parser.parse_args(["--expected-vertical", "vehicle_camera_system"])
    assert args.section == []
    assert args.all_sections is False
    assert args.recheck_wait_ms == 800
    assert args.no_multi_value is False
    assert makro_coverage._target_sections(
        type("Adapter", (), {})(), args
    ) == ["Additional Description"]


def test_coverage_cli_has_no_save_submit_or_browser_close_path():
    source = inspect.getsource(makro_coverage.main)
    assert 'get_by_text("Save"' not in source
    assert 'get_by_text("Send to QC"' not in source
    assert "browser.close()" not in source
    assert "context.close()" not in source
    assert "harness.detach()" in source
