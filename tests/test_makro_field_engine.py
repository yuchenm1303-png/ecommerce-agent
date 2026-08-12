from __future__ import annotations

import pytest

from app.makro.field_engine import (
    execution_contract,
    fill_control,
    fill_radio_group,
    radio_group_values_equivalent,
    read_control,
    read_radio_group,
    values_equivalent,
)
from app.makro_dryrun import fill_resolved_field
from app.resolution_types import ResolvedAnswer


class SelectedOption:
    def __init__(self, owner):
        self.owner = owner

    def inner_text(self, timeout=None):
        return self.owner.selected_label


class Locator:
    def __init__(self, *, value="", text="", attrs=None):
        self.first = self
        self.value = value
        self.text = text
        self.attrs = dict(attrs or {})
        self.checked = False
        self.visible = True
        self._count = 1
        self.events: list[str] = []
        self.blurred = False
        self.clicked = 0

    def count(self):
        return self._count

    def is_visible(self):
        return self.visible

    def wait_for(self, state="visible"):
        assert state == "visible"

    def fill(self, value):
        self.value = value

    def input_value(self, timeout=None):
        return self.value

    def inner_text(self, timeout=None):
        return self.text

    def get_attribute(self, name, timeout=None):
        if name == "value":
            return self.attrs.get(name, self.value)
        return self.attrs.get(name)

    def select_option(self, label=None, value=None):
        if label is not None:
            self.selected_label = label
            self.value = label
        else:
            self.selected_label = value
            self.value = value

    def locator(self, selector):
        assert selector == "option:checked"
        return SelectedOption(self)

    def dispatch_event(self, event):
        self.events.append(event)

    def blur(self):
        self.blurred = True

    def check(self):
        self.checked = True

    def uncheck(self):
        self.checked = False

    def is_checked(self):
        return self.checked

    def click(self):
        self.clicked += 1


class CandidateSet:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class Page:
    def __init__(self, controls=None, text_candidates=None):
        self.controls = dict(controls or {})
        self.text_candidates = dict(text_candidates or {})

    def locator(self, selector):
        key = selector.split(" >> ")[-1]
        return self.controls[key]

    def get_by_text(self, text, exact=True):
        assert exact is True
        return CandidateSet(self.text_candidates.get(text, []))

    def wait_for_timeout(self, ms):
        return None


def control(name, kind="input", **extra):
    item = {
        "name": name,
        "field_kind": kind,
        "path": f"body > input[name='{name}']",
        "selector_candidates": [],
    }
    item.update(extra)
    return item


def semantic(key, controls, *, multi=False, execution_family=""):
    return {
        "attribute_key": key,
        "label": key,
        "multi_value": multi,
        "controls": controls,
        "execution_family": execution_family,
    }


def resolved(key, values, qualifier=None):
    return ResolvedAnswer(
        attribute_key=key,
        label=key,
        status="resolved",
        answer=" | ".join(values),
        answer_values=values,
        qualifier=qualifier,
        source_type="fixture",
        source_reference="fixture",
        evidence="fixture",
        confidence=1.0,
    )


def test_execution_contract_uses_live_dom_but_preserves_registry_family_metadata():
    field = semantic(
        "depth",
        [control("depth_0_value", type="number", inputmode="decimal")],
        execution_family="numeric_qualified",
    )
    answer = resolved("depth", ["12.5"], qualifier="cm")
    field["controls"].append(control("depth_0_qualifier", kind="select"))
    contract = execution_contract(field, answer)
    assert contract.live_family == "numeric_qualified"
    assert contract.schema_family == "numeric_qualified"
    assert contract.supported is True


def test_number_control_rejects_text_before_any_dom_fill():
    c = control("shipping_days_0_value", type="number")
    locator = Locator()
    page = Page({'[name="shipping_days_0_value"]': locator})
    with pytest.raises(ValueError, match="非数字"):
        fill_control(page, c, "N/A")
    assert locator.value == ""


def test_native_select_can_accept_canonical_value_and_verify_display_label():
    c = control(
        "listing_status_0_value",
        kind="select",
        options=[
            {"text": "Active", "value": "ACTIVE", "disabled": False},
            {"text": "Inactive", "value": "INACTIVE", "disabled": False},
        ],
    )
    locator = Locator()
    page = Page({'[name="listing_status_0_value"]': locator})
    fill_control(page, c, "ACTIVE")
    assert locator.selected_label == "Active"
    assert locator.events == ["input", "change"]
    assert locator.blurred is True
    assert read_control(page, c) == "Active"
    assert values_equivalent(c, "ACTIVE", "Active") is True


def test_custom_dropdown_refuses_ambiguous_visible_exact_option_instead_of_clicking_last():
    c = control("colour_0_value", kind="dropdown")
    trigger = Locator()
    first = Locator(text="Black")
    second = Locator(text="Black")
    page = Page(
        {'[name="colour_0_value"]': trigger},
        {"Black": [first, second]},
    )
    with pytest.raises(RuntimeError, match="可见精确匹配数=2"):
        fill_control(page, c, "Black")
    assert first.clicked == 0
    assert second.clicked == 0


def test_contenteditable_uses_text_readback_not_input_value():
    c = control("description_0_value", kind="contenteditable")
    locator = Locator(value="wrong-input-value", text="Rich description")
    page = Page({'[name="description_0_value"]': locator})
    assert read_control(page, c) == "Rich description"


def test_boolean_adapter_requires_explicit_boolean_and_reads_checked_state():
    c = control("forbid_shipping_0_value", kind="checkbox")
    locator = Locator()
    page = Page({'[name="forbid_shipping_0_value"]': locator})
    fill_control(page, c, "yes")
    assert read_control(page, c) == "true"
    assert values_equivalent(c, "yes", "true") is True
    with pytest.raises(ValueError, match="无法机械解释"):
        fill_control(page, c, "sometimes")


def test_radio_group_selects_exact_one_live_option_and_reads_it_back():
    red = control("colour", kind="radio", id="red", label="Red", value="RED")
    blue = control("colour", kind="radio", id="blue", label="Blue", value="BLUE")
    red_locator = Locator(attrs={"value": "RED"})
    blue_locator = Locator(attrs={"value": "BLUE"})
    page = Page(
        {
            '[name="colour"]': red_locator,
        }
    )
    # Give the radios deterministic unique selectors, as the live scanner does.
    red["name"] = "colour_red"
    blue["name"] = "colour_blue"
    page.controls = {
        '[name="colour_red"]': red_locator,
        '[name="colour_blue"]': blue_locator,
    }

    selector = fill_radio_group(page, [red, blue], "Blue")
    assert selector == '[name="colour_blue"]'
    assert blue_locator.checked is True
    assert read_radio_group(page, [red, blue]) == "BLUE"
    assert radio_group_values_equivalent([red, blue], "Blue", "BLUE") is True


def test_readonly_and_disabled_controls_fail_closed():
    for metadata in ({"readonly": True}, {"disabled": True}):
        c = control("model_number_0_value", **metadata)
        locator = Locator()
        page = Page({'[name="model_number_0_value"]': locator})
        with pytest.raises(RuntimeError):
            fill_control(page, c, "M8")
        assert locator.value == ""


def test_dryrun_reports_generic_execution_family_and_rejects_unknown_before_write():
    unknown = control("mystery_0_value", kind="unknown")
    locator = Locator()
    page = Page({'[name="mystery_0_value"]': locator})
    result = fill_resolved_field(
        page,
        semantic("mystery", [unknown]),
        resolved("mystery", ["anything"]),
    )
    assert result.status == "validation_failed"
    assert result.execution_family == "unsupported"
    assert locator.value == ""
