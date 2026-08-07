from __future__ import annotations

import pytest

from app.answer_resolver import ResolvedAnswer
from app.makro_dryrun import fill_resolved_field, selector_for_control


class FakeSelectedOption:
    def __init__(self, owner):
        self.owner = owner

    def inner_text(self, timeout=None):
        return self.owner.selected_label


class FakeLocator:
    def __init__(self, value="", selected_label=""):
        self.value = value
        self.selected_label = selected_label
        self.checked = False
        self.visible = True
        self._count = 1
        self.first = self

    def wait_for(self, state="visible"):
        assert state == "visible"

    def fill(self, value):
        self.value = value

    def select_option(self, label=None, value=None):
        if label is not None:
            self.selected_label = label
            self.value = label
        elif value is not None:
            self.selected_label = value
            self.value = value

    def locator(self, selector):
        assert selector == "option:checked"
        return FakeSelectedOption(self)

    def input_value(self, timeout=None):
        return self.value

    def get_attribute(self, name, timeout=None):
        assert name == "value"
        return self.value

    def check(self):
        self.checked = True

    def uncheck(self):
        self.checked = False

    def is_checked(self):
        return self.checked

    def count(self):
        return self._count

    def is_visible(self):
        return self.visible


class FakePage:
    def __init__(self, controls, on_wait_timeout=None):
        self.controls = controls
        self.on_wait_timeout = on_wait_timeout
        self.used_selectors: list[str] = []

    def locator(self, selector):
        self.used_selectors.append(selector)
        key = selector.split(" >> ")[-1]
        return self.controls[key]

    def wait_for_timeout(self, ms):
        if self.on_wait_timeout:
            self.on_wait_timeout()


def control(name, kind="input"):
    return {
        "name": name,
        "field_kind": kind,
        "path": f"body > input[name='{name}']",
        "selector_candidates": [],
    }


def semantic(key, controls, multi_value=False):
    return {
        "attribute_key": key,
        "label": key.replace("_", " ").title(),
        "multi_value": multi_value,
        "controls": controls,
    }


def resolved(key, values, qualifier=None):
    return ResolvedAnswer(
        attribute_key=key,
        label=key,
        status="resolved",
        answer=" | ".join(values),
        answer_values=values,
        qualifier=qualifier,
        source_type="structured",
        source_reference="fixture",
        evidence=str(values),
        confidence=1.0,
    )


def test_selector_prefers_unique_name_over_duplicate_id():
    item = {
        "id": "sales_package",
        "name": "sales_package_2_value",
        "path": "body > input:nth-child(3)",
        "selector_candidates": ["#sales_package"],
    }

    assert selector_for_control(item) == '[name="sales_package_2_value"]'


def test_single_input_fill_and_readback_passes():
    c = control("model_number_0_value")
    page = FakePage({'[name="model_number_0_value"]': FakeLocator()})

    result = fill_resolved_field(page, semantic("model_number", [c]), resolved("model_number", ["L11"]))

    assert result.status == "validated"
    assert result.actual == ["L11"]


def test_multi_value_maps_each_value_to_its_own_slot():
    controls = [
        control("sales_package_0_value"),
        control("sales_package_1_value"),
        control("sales_package_2_value"),
    ]
    page = FakePage(
        {
            '[name="sales_package_0_value"]': FakeLocator(),
            '[name="sales_package_1_value"]': FakeLocator(),
            '[name="sales_package_2_value"]': FakeLocator(),
        }
    )

    result = fill_resolved_field(
        page,
        semantic("sales_package", controls, multi_value=True),
        resolved("sales_package", ["Camera", "Cable", "Manual"]),
    )

    assert result.status == "validated"
    assert result.actual == ["Camera", "Cable", "Manual"]


def test_more_values_than_slots_never_invents_or_overwrites():
    controls = [control("ports_0_value"), control("ports_1_value")]
    page = FakePage(
        {
            '[name="ports_0_value"]': FakeLocator(),
            '[name="ports_1_value"]': FakeLocator(),
        }
    )

    result = fill_resolved_field(
        page,
        semantic("ports", controls, multi_value=True),
        resolved("ports", ["USB-C", "HDMI", "AV"]),
    )

    assert result.status == "validation_failed"
    assert result.actual == ["USB-C", "HDMI"]
    assert "3" in result.detail


def test_select_fill_reads_selected_label():
    c = control("colour_0_value", kind="select")
    page = FakePage({'[name="colour_0_value"]': FakeLocator()})

    result = fill_resolved_field(page, semantic("colour", [c]), resolved("colour", ["Black"]))

    assert result.status == "validated"
    assert result.actual == ["Black"]


def test_non_resolved_answer_is_skipped_without_touching_page():
    c = control("model_number_0_value")
    page = FakePage({})
    answer = ResolvedAnswer(attribute_key="model_number", label="Model Number", status="missing")

    result = fill_resolved_field(page, semantic("model_number", [c]), answer)

    assert result.status == "skipped"


def test_duplicate_same_name_control_anywhere_is_refused():
    """Regression: a global [name=...] matching >1 DOM instance must never write/read."""
    c = control("warranty_service_type_0_value")
    loc = FakeLocator()
    loc._count = 2
    page = FakePage({'[name="warranty_service_type_0_value"]': loc})

    result = fill_resolved_field(
        page, semantic("warranty_service_type", [c]), resolved("warranty_service_type", ["Standard"])
    )

    assert result.status == "fill_error"
    assert "2" in result.detail
    assert loc.value == ""


def test_invisible_control_is_refused():
    c = control("warranty_service_type_0_value")
    loc = FakeLocator()
    loc.visible = False
    page = FakePage({'[name="warranty_service_type_0_value"]': loc})

    result = fill_resolved_field(
        page, semantic("warranty_service_type", [c]), resolved("warranty_service_type", ["Standard"])
    )

    assert result.status == "fill_error"
    assert '[name="warranty_service_type_0_value"]' in result.detail
    assert loc.value == ""


def test_section_path_scopes_selector_for_fill_and_readback():
    c = control("warranty_summary_0_value")
    page = FakePage({'[name="warranty_summary_0_value"]': FakeLocator()})

    result = fill_resolved_field(
        page,
        semantic("warranty_summary", [c]),
        resolved("warranty_summary", ["24 months"]),
        section_path="body > div#additional-description-card",
    )

    assert result.status == "validated"
    scoped = 'body > div#additional-description-card >> [name="warranty_summary_0_value"]'
    assert scoped in page.used_selectors
    assert result.selectors and result.selectors[0] == scoped


def test_react_rerender_reset_is_not_reported_validated():
    """Regression: immediate readback OK + value reset after render cycle => not validated."""
    c = control("warranty_service_type_0_value")
    loc = FakeLocator()

    def reset_on_render_cycle():
        loc.value = ""

    page = FakePage(
        {'[name="warranty_service_type_0_value"]': loc},
        on_wait_timeout=reset_on_render_cycle,
    )

    result = fill_resolved_field(
        page, semantic("warranty_service_type", [c]), resolved("warranty_service_type", ["Standard"])
    )

    assert result.status == "validation_failed"
    assert result.actual == ["Standard"]
    assert "React" in result.detail


def test_control_that_vanishes_after_render_cycle_is_not_validated():
    c = control("warranty_summary_0_value")
    loc = FakeLocator(value="24 months")

    def vanish():
        loc._count = 0

    page = FakePage(
        {'[name="warranty_summary_0_value"]': loc},
        on_wait_timeout=vanish,
    )

    result = fill_resolved_field(
        page, semantic("warranty_summary", [c]), resolved("warranty_summary", ["24 months"])
    )

    assert result.status == "validation_failed"
    assert "React" in result.detail
