"""Makro domain adapter / skill layer tests (offline, no real browser)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.answer_resolver import RESOLVED, resolve_fields
from app.makro import base_section_title, build_semantic_fields, parse_makro_listing_url
from app.makro.domain import MakroDomainAdapter
from app.source_bundle import ProductSourceBundle


class FakeSelectedOption:
    def __init__(self, owner):
        self.owner = owner

    def inner_text(self):
        return self.owner.selected_label


class FakeLocator:
    def __init__(self, value="", selected_label=""):
        self.value = value
        self.selected_label = selected_label
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

    def input_value(self):
        return self.value


class FakePage:
    def __init__(self, controls):
        self.controls = controls

    def locator(self, selector):
        return self.controls[selector]


def _control(name, kind="input"):
    return {
        "name": name,
        "field_kind": kind,
        "path": "body > input[name='{0}']".format(name),
        "selector_candidates": [],
    }


def _semantic(key, controls, multi_value=False):
    return {
        "attribute_key": key,
        "label": key.replace("_", " ").title(),
        "multi_value": multi_value,
        "controls": controls,
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

    assert (
        adapter.base_section_title("Additional Description (Optional) (0/46)")
        == "Additional Description"
    )
    assert base_section_title("Price, Stock and Shipping Information (14/14)") == (
        "Price, Stock and Shipping Information"
    )


def test_adapter_discovers_sections_from_page():
    sections = [
        {"title": "Product Description (0/14)", "path": "body > div#pd"},
        {"title": "Additional Description (Optional) (0/46)", "path": "body > div#ad"},
    ]
    page = SimpleNamespace(
        url="https://seller.makro.co.za/", evaluate=lambda script, *a, **k: sections
    )
    adapter = MakroDomainAdapter(page)

    found = adapter.find_sections()
    assert found == sections
    assert adapter.find_section("Product Description")["path"] == "body > div#pd"
    assert adapter.find_section("Additional Description")["path"] == "body > div#ad"


def test_adapter_builds_semantic_fields_and_locator_strategy():
    controls = [
        {**_control("sku_id"), "id": "sku_id", "label": "SKU ID"},
        {**_control("listing_status_0_value"), "id": "listing_status", "label": "Listing Status"},
    ]
    adapter = MakroDomainAdapter(_listing_page())

    fields = adapter.build_semantic_fields(controls)
    assert {f["attribute_key"] for f in fields} == {"sku_id", "listing_status"}
    assert adapter.selector_for(controls[0]) == '[name="sku_id"]'


def test_acceptance_chain_resolver_section_selection_fill_readback():
    """Freeze resolver -> section selection -> fill -> readback (offline).

    Field metadata mirrors the real vehicle_camera_system fixture structure;
    nothing is category-hard-coded here.
    """
    import makro_fill

    controls = [
        {**_control("sku_id"), "id": "sku_id", "label": "SKU ID",
         "section_heading": "Price, Stock and Shipping Information"},
        {**_control("listing_status_0_value"), "id": "listing_status", "label": "Listing Status",
         "field_kind": "select",
         "section_heading": "Price, Stock and Shipping Information",
         "options": [{"text": "Draft", "value": "Draft"}, {"text": "Active", "value": "Active"}]},
        {**_control("model_number_0_value"), "id": "model_number", "label": "Model Number",
         "section_heading": "Product Description"},
    ]
    fields = build_semantic_fields(controls)
    assert {f["attribute_key"] for f in fields} == {"sku_id", "listing_status", "model_number"}

    bundle = ProductSourceBundle(sku="SKU-001")
    bundle.add_evidence(key="SKU", value="SKU-001", source_type="structured",
                        source_reference="table.xlsx:row=2", priority=10)
    bundle.add_evidence(key="Listing Status", value="Active", source_type="structured",
                        source_reference="table.xlsx:row=2", priority=10)
    bundle.add_evidence(key="Model Number", value="VC-9", source_type="structured",
                        source_reference="table.xlsx:row=2", priority=10)

    answers = resolve_fields(fields, bundle)
    by_key = {a.attribute_key: a for a in answers}
    assert by_key["sku_id"].status == RESOLVED
    assert by_key["listing_status"].status == RESOLVED
    assert by_key["model_number"].status == RESOLVED

    sections_payload = [
        {"title": "Price, Stock and Shipping Information (14/14)"},
        {"title": "Product Description (0/14)"},
    ]
    resolutions = []
    for field in fields:
        data = by_key[field["attribute_key"]].as_dict()
        data["section_heading"] = field.get("section_heading") or ""
        resolutions.append(data)
    target = makro_fill._select_target_section(sections_payload, resolutions, None)
    assert target == "Price, Stock and Shipping Information"

    page = FakePage(
        {
            '[name="sku_id"]': FakeLocator(),
            '[name="listing_status_0_value"]': FakeLocator(),
            '[name="model_number_0_value"]': FakeLocator(),
        }
    )
    adapter = MakroDomainAdapter(page)
    for field in fields:
        answer = by_key[field["attribute_key"]]
        if answer.status != RESOLVED:
            continue
        result = adapter.fill_resolved_field(field, answer)
        assert result.status == "validated", result.detail
    assert page.controls['[name="sku_id"]'].value == "SKU-001"
    assert page.controls['[name="listing_status_0_value"]'].selected_label == "Active"
    assert page.controls['[name="model_number_0_value"]'].value == "VC-9"


def test_adapter_has_no_save_or_submit_path():
    import inspect

    from app.makro import domain

    source = inspect.getsource(domain)
    assert "def save(" not in source
    assert 'get_by_text("Save"' not in source
    assert 'get_by_text("Send to QC"' not in source
    assert "context.close()" not in source
    assert "browser.close()" not in source
