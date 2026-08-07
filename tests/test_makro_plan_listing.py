from __future__ import annotations

import inspect

import makro_plan_listing


def test_live_planner_requires_expected_vertical():
    parser = makro_plan_listing.build_parser()
    args = parser.parse_args(["--qa", "qa.xlsx", "--expected-vertical", "vehicle_camera_system"])
    assert args.expected_vertical == "vehicle_camera_system"


def test_live_planner_contains_no_browser_fill_or_save_path():
    source = inspect.getsource(makro_plan_listing.main)
    assert "fill_resolved_field(" not in source
    assert "exercise_live_field(" not in source
    assert 'get_by_text("Save"' not in source
    assert 'get_by_text("Send to QC"' not in source
    assert "save_section(" not in source


def test_live_planner_blocks_unsaved_expanded_section():
    source = inspect.getsource(makro_plan_listing._assert_no_unsaved_section)
    assert "has_edit" in source
    assert "planner 已停止" in source
