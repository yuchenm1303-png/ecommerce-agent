from __future__ import annotations

from app.makro_dryrun import fill_resolved_field
from app.resolution_types import ResolvedAnswer


class SelectedOption:
    def __init__(self, owner):
        self.owner = owner

    def inner_text(self, timeout=None):
        return self.owner.selected_label


class Locator:
    def __init__(self, *, value="", selected_label="", options=None, events=None):
        self.first = self
        self.value = value
        self.selected_label = selected_label
        self.options = [dict(item) for item in (options or [])]
        self.events = events if events is not None else []
        self._count = 1
        self.visible = True

    def count(self):
        return self._count

    def is_visible(self):
        return self.visible

    def wait_for(self, state="visible"):
        assert state == "visible"

    def fill(self, value):
        self.events.append("value_fill")
        self.value = value

    def input_value(self, timeout=None):
        return self.value

    def evaluate(self, _script):
        return [dict(item) for item in self.options]

    def select_option(self, label=None, value=None):
        selected = label if label is not None else value
        self.events.append("qualifier_select")
        self.selected_label = str(selected or "")
        self.value = str(selected or "")

    def locator(self, selector):
        assert selector == "option:checked"
        return SelectedOption(self)

    def get_attribute(self, name, timeout=None):
        if name == "value":
            return self.value
        return None

    def dispatch_event(self, _event):
        return None

    def blur(self):
        return None


class RerenderingQualifier(Locator):
    def __init__(self, page, value_selector, *, options, events):
        super().__init__(options=options, events=events)
        self.page = page
        self.value_selector = value_selector

    def select_option(self, label=None, value=None):
        super().select_option(label=label, value=value)
        # Model Makro React behaviour: changing the unit destroys the old
        # dependent numeric input and mounts a fresh empty one.
        self.page.controls[self.value_selector] = Locator(events=self.events)


class NoRewriteQualifier(Locator):
    def select_option(self, label=None, value=None):
        raise AssertionError("already-correct qualifier must not be selected again")


class Page:
    def __init__(self):
        self.controls = {}
        self.waits = []

    def locator(self, selector):
        key = selector.split(" >> ")[-1]
        return self.controls[key]

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


def control(name, kind="input", **extra):
    item = {
        "name": name,
        "field_kind": kind,
        "path": f"body > input[name='{name}']",
        "selector_candidates": [],
    }
    item.update(extra)
    return item


def resolved(key, value, qualifier):
    return ResolvedAnswer(
        attribute_key=key,
        label=key,
        status="resolved",
        answer=value,
        answer_values=[value],
        qualifier=qualifier,
        source_type="fixture",
        source_reference="fixture",
        evidence="fixture",
        confidence=1.0,
    )


def field(key, value_control, qualifier_control):
    return {
        "attribute_key": key,
        "label": key,
        "multi_value": False,
        "controls": [value_control, qualifier_control],
        "execution_family": "numeric_qualified",
    }


def test_qualified_transaction_sets_unit_before_value_and_rebinds_after_rerender():
    events = []
    page = Page()
    value_selector = '[name="breadth_0_value"]'
    qualifier_selector = '[name="breadth_0_qualifier"]'
    value_control = control("breadth_0_value", type="number", inputmode="decimal")
    options = [
        {"text": "cm", "value": "cm", "disabled": False},
        {"text": "mm", "value": "mm", "disabled": False},
    ]
    qualifier_control = control("breadth_0_qualifier", kind="select", options=options)

    old_value_locator = Locator(value="stale", events=events)
    page.controls[value_selector] = old_value_locator
    page.controls[qualifier_selector] = RerenderingQualifier(
        page,
        value_selector,
        options=options,
        events=events,
    )

    result = fill_resolved_field(
        page,
        field("breadth", value_control, qualifier_control),
        resolved("breadth", "12.5", "cm"),
        recheck_wait_ms=100,
    )

    assert result.status == "validated"
    assert events.index("qualifier_select") < events.index("value_fill")
    assert page.controls[value_selector] is not old_value_locator
    assert page.controls[value_selector].value == "12.5"
    assert result.actual == ["12.5"]


def test_qualified_transaction_does_not_reselect_already_correct_unit():
    events = []
    page = Page()
    value_selector = '[name="height_0_value"]'
    qualifier_selector = '[name="height_0_qualifier"]'
    value_control = control("height_0_value", type="number", inputmode="decimal")
    options = [{"text": "cm", "value": "cm", "disabled": False}]
    qualifier_control = control("height_0_qualifier", kind="select", options=options)

    page.controls[value_selector] = Locator(events=events)
    page.controls[qualifier_selector] = NoRewriteQualifier(
        value="cm",
        selected_label="cm",
        options=options,
        events=events,
    )

    result = fill_resolved_field(
        page,
        field("height", value_control, qualifier_control),
        resolved("height", "20", "cm"),
        recheck_wait_ms=100,
    )

    assert result.status == "validated"
    assert "qualifier_select" not in events
    assert events == ["value_fill"]
    assert page.controls[value_selector].value == "20"
