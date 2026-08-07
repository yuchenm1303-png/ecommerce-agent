from __future__ import annotations

from app.answer_resolver import ResolvedAnswer
from app.makro_dryrun import fill_resolved_field, selector_for_control


class FakeSelectedOption:
    def __init__(self, owner):
        self.owner = owner

    def inner_text(self):
        return self.owner.selected_label


class FakeLocator:
    def __init__(self, value="", selected_label=""):
        self.value = value
        self.selected_label = selected_label
        self.checked = False
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

    def input_value(self):
        return self.value

    def check(self):
        self.checked = True

    def uncheck(self):
        self.checked = False

    def is_checked(self):
        return self.checked


class FakePage:
    def __init__(self, controls):
        self.controls = controls

    def locator(self, selector):
        return self.controls[selector]


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
    assert "3 个值" in result.detail


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
