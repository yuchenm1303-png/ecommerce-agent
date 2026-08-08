"""Makro domain adapter tests (offline, no real browser)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.makro import base_section_title, build_semantic_fields
from app.makro.domain import MakroDomainAdapter
from app.resolution_types import RESOLVED, ResolvedAnswer


class FakeSelectedOption:
    def __init__(self, owner):
        self.owner = owner

    def inner_text(self, timeout=None):
        return self.owner.selected_label


class FakeLocator:
    def __init__(self, value="", selected_label=""):
        self.value = value
        self.selected_label = selected_label
        self.visible = True
        self._count = 1
        self.first = self

    def wait_for(self, state="visible"):
        assert state == "visible"

    def fill(self, value):
        self.value = value

    def select_option(self, label=None, value=None):
        self.selected_label = label or value or self.selected_label
        self.value = self.selected_label

    def locator(self, selector):
        assert selector == "option:checked"
        return FakeSelectedOption(self)

    def input_value(self, timeout=None):
        return self.value

    def get_attribute(self, name, timeout=None):
        assert name == "value"
        return self.value

    def count(self):
        return self._count

    def is_visible(self):
        return self.visible


class FakePage:
    def __init__(self, controls):
        self.controls = controls

    def locator(self, selector):
        key = selector.split(" >> ")[-1]
        return self.controls[key]

    def wait_for_timeout(self, ms):
        pass


def _control(name, kind="input"):
    return {
        "name": name,
        "field_kind": kind,
        "path": "body > input[name='{0}']".format(name),
        "selector_candidates": [],
    }


def _listing_page(vertical="vehicle_camera_system"):
    return SimpleNamespace(
        url=(
            "https://seller.makro.co.za/index.html#dashboard/addListings/single"
            "?vertical={0}".format(vertical)
        ),
        locator=lambda selector: SimpleNamespace(count=lambda: 0),
        get_by_text=lambda text, exact=False: SimpleNamespace(count=lambda: 1),
    )


def test_adapter_recognizes_listing_and_parses_target():
    adapter = MakroDomainAdapter(_listing_page())
    assert adapter.is_listing_page() is True
    target = adapter.current_target()
    assert target is not None
    assert target.vertical == "vehicle_camera_system"


def test_adapter_vertical_guard_fails_closed_before_fill():
    adapter = MakroDomainAdapter(_listing_page(vertical="sports_action_camera"))
    with pytest.raises(RuntimeError, match="vertical"):
        adapter.assert_expected_vertical("vehicle_camera_system")


def test_adapter_normalizes_section_titles():
    adapter = MakroDomainAdapter(_listing_page())
    assert adapter.base_section_title("Additional Description (Optional) (0/46)") == "Additional Description"
    assert base_section_title("Price, Stock and Shipping Information (14/14)") == "Price, Stock and Shipping Information"


def test_adapter_discovers_sections_from_page():
    sections = [
        {"title": "Product Description (0/14)", "path": "body > div#pd"},
        {"title": "Additional Description (Optional) (0/46)", "path": "body > div#ad"},
    ]
    page = SimpleNamespace(
        url="https://seller.makro.co.za/", evaluate=lambda script, *a, **k: sections
    )
    adapter = MakroDomainAdapter(page)
    assert adapter.find_sections() == sections
    assert adapter.find_section("Product Description")["path"] == "body > div#pd"
    assert adapter.find_section("Additional Description")["path"] == "body > div#ad"


def test_adapter_builds_semantic_fields_and_locator_strategy():
    controls = [
        {**_control("sku_id"), "id": "sku_id", "label": "SKU ID"},
        {
            **_control("listing_status_0_value"),
            "id": "listing_status",
            "label": "Listing Status",
        },
    ]
    adapter = MakroDomainAdapter(_listing_page())
    fields = adapter.build_semantic_fields(controls)
    assert {field["attribute_key"] for field in fields} == {"sku_id", "listing_status"}
    assert adapter.selector_for(controls[0]) == '[name="sku_id"]'


def test_adapter_fills_and_reads_browser_execution_shape_without_resolver():
    controls = [
        {
            **_control("model_number_0_value"),
            "id": "model_number",
            "label": "Model Number",
            "section_heading": "Product Description",
        },
        {
            **_control("listing_status_0_value", "select"),
            "id": "listing_status",
            "label": "Listing Status",
            "section_heading": "Price, Stock and Shipping Information",
            "options": [
                {"text": "Draft", "value": "Draft"},
                {"text": "Active", "value": "Active"},
            ],
        },
    ]
    fields = build_semantic_fields(controls)
    page = FakePage(
        {
            '[name="model_number_0_value"]': FakeLocator(),
            '[name="listing_status_0_value"]': FakeLocator(),
        }
    )
    adapter = MakroDomainAdapter(page)
    answers = {
        "model_number": ResolvedAnswer(
            attribute_key="model_number",
            label="Model Number",
            status=RESOLVED,
            answer="VC-9",
            answer_values=["VC-9"],
        ),
        "listing_status": ResolvedAnswer(
            attribute_key="listing_status",
            label="Listing Status",
            status=RESOLVED,
            answer="Active",
            answer_values=["Active"],
        ),
    }

    for semantic_field in fields:
        result = adapter.fill_resolved_field(
            semantic_field,
            answers[semantic_field["attribute_key"]],
        )
        assert result.status == "validated", result.detail

    assert page.controls['[name="model_number_0_value"]'].value == "VC-9"
    assert page.controls['[name="listing_status_0_value"]'].selected_label == "Active"


def test_adapter_has_no_send_to_qc_or_browser_close_path():
    import inspect
    from app.makro import domain

    source = inspect.getsource(domain)
    assert 'get_by_text("Send to QC"' not in source
    assert "context.close()" not in source
    assert "browser.close()" not in source
