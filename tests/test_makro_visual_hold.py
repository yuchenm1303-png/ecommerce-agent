from __future__ import annotations

import pytest

import makro_coverage
from app.makro import visual_hold
from app.makro.coverage import FAIL, PASS, SKIPPED_EXISTING, CoverageResult


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


def _field(key: str, label: str, control: dict):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": "Additional Description (Optional) (0/46)",
        "subsection_heading": "GENERAL",
        "controls": [control],
    }


class _FakePage:
    def __init__(self):
        self.waits: list[int] = []

    def wait_for_timeout(self, value: int):
        self.waits.append(value)


class _FakeAdapter:
    def __init__(self, fields):
        self.fields = fields
        self.page = _FakePage()
        self.expanded = False
        self.open_count = 0
        self.scan_count = 0

    def find_section(self, title):
        return {
            "title": title,
            "path": "section-path",
            "has_edit": not self.expanded,
        }

    def open_section_for_edit(self, section):
        assert section["has_edit"] is True
        self.expanded = True
        self.open_count += 1

    def scan_section_fields(self, section_path, **kwargs):
        assert section_path == "section-path"
        self.scan_count += 1
        return [{"captured": True}]

    def build_semantic_fields(self, controls):
        return self.fields


def test_visual_hold_opens_section_once_and_leaves_successful_values_for_inspection(monkeypatch):
    empty = _field("empty", "Empty", _control(name="empty_0_value", label="Empty"))
    existing = _field(
        "existing",
        "Existing",
        _control(
            name="existing_0_value",
            label="Existing",
            value="REAL DATA",
            value_recorded=True,
        ),
    )
    adapter = _FakeAdapter([empty, existing])
    calls = []

    def fake_exercise(adapter_arg, field_item, section_path, ordinal, **kwargs):
        calls.append(
            {
                "key": field_item["attribute_key"],
                "section_path": section_path,
                "ordinal": ordinal,
                **kwargs,
            }
        )
        return CoverageResult(
            section="Additional Description",
            subsection="GENERAL",
            attribute_key=field_item["attribute_key"],
            label=field_item["label"],
            shape="text",
            status=PASS,
            candidate=["COVERAGE_001"],
            immediate=["COVERAGE_001"],
            settled=["COVERAGE_001"],
        )

    monkeypatch.setattr(visual_hold, "exercise_live_field", fake_exercise)
    monkeypatch.setattr(visual_hold, "_verify_final_hold", lambda *args, **kwargs: None)

    results = visual_hold.fill_section_for_visual_hold(adapter, "Additional Description")

    assert adapter.open_count == 1
    assert adapter.expanded is True  # caller/user gets an open section to inspect
    assert len(calls) == 1
    assert calls[0]["key"] == "empty"
    assert calls[0]["exercise_multi_value"] is False
    assert {item.status for item in results} == {PASS, SKIPPED_EXISTING}


def test_visual_hold_cancels_immediately_if_population_raises(monkeypatch):
    empty = _field("empty", "Empty", _control(name="empty_0_value", label="Empty"))
    adapter = _FakeAdapter([empty])
    cancelled = []

    def explode(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    def fake_cancel(adapter_arg, section_title, **kwargs):
        cancelled.append(section_title)
        adapter_arg.expanded = False

    monkeypatch.setattr(visual_hold, "exercise_live_field", explode)
    monkeypatch.setattr(visual_hold, "cancel_section", fake_cancel)

    with pytest.raises(RuntimeError, match="synthetic failure"):
        visual_hold.fill_section_for_visual_hold(adapter, "Additional Description")

    assert cancelled == ["Additional Description"]
    assert adapter.expanded is False


def test_final_hold_verification_downgrades_a_value_reset_by_later_react_update(monkeypatch):
    control = _control(name="field_0_value", label="Field")
    live_field = _field("field", "Field", control)
    adapter = _FakeAdapter([live_field])
    adapter.expanded = True
    result = CoverageResult(
        section="Additional Description",
        subsection="GENERAL",
        attribute_key="field",
        label="Field",
        shape="text",
        status=PASS,
        candidate=["COVERAGE_001"],
        immediate=["COVERAGE_001"],
        settled=["COVERAGE_001"],
    )

    monkeypatch.setattr(
        visual_hold,
        "_unique_visible_locator",
        lambda *args, **kwargs: (object(), '[name="field_0_value"]'),
    )
    monkeypatch.setattr(visual_hold, "_read_control", lambda *args, **kwargs: "RESET")

    visual_hold._verify_final_hold(
        adapter,
        "Additional Description",
        "section-path",
        [result],
        wait_ms=0,
        max_scroll_steps=1,
    )

    assert result.status == FAIL
    assert "最终整页复核" in result.detail
    assert "RESET" in result.detail


def test_visual_hold_cli_is_single_section_only():
    parser = makro_coverage.build_parser()
    args = parser.parse_args(
        ["--expected-vertical", "vehicle_camera_system", "--visual-hold"]
    )
    assert args.visual_hold is True
    assert makro_coverage._validate_visual_hold_sections(
        args, ["Additional Description"]
    ) == "Additional Description"

    args_all = parser.parse_args(
        [
            "--expected-vertical",
            "vehicle_camera_system",
            "--visual-hold",
            "--all-sections",
        ]
    )
    with pytest.raises(RuntimeError, match="只允许一个 section"):
        makro_coverage._validate_visual_hold_sections(
            args_all, ["Product Description", "Additional Description"]
        )


def test_visual_hold_code_has_no_save_or_submit_click_path():
    source = open(visual_hold.__file__, encoding="utf-8").read()
    assert 'get_by_text("Save"' not in source
    assert 'get_by_text("Send to QC"' not in source
    assert "browser.close()" not in source
    assert "context.close()" not in source
