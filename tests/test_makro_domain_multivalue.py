from __future__ import annotations

from types import SimpleNamespace

from app.makro.domain import MakroDomainAdapter


class FakePage:
    def __init__(self):
        self.waits = []

    def wait_for_timeout(self, value):
        self.waits.append(value)


def _field(slot_count: int):
    return {
        "attribute_key": "sales_package",
        "label": "Sales Package",
        "section_heading": "Product Description",
        "controls": [
            {
                "name": f"sales_package_{index}_value",
                "field_kind": "input",
            }
            for index in range(slot_count)
        ],
    }


def test_domain_expands_plus_slots_until_answer_fits(monkeypatch):
    page = FakePage()
    adapter = MakroDomainAdapter(page)
    original = _field(1)
    refreshed = _field(2)
    answer = SimpleNamespace(answer_values=["Camera", "Manual"])
    clicks = []

    def fake_click(page_arg, section_path, control):
        clicks.append((section_path, control["name"]))
        return {"available": True, "clicked": True, "reason": ""}

    monkeypatch.setattr("app.makro.domain.click_add_value_for_control", fake_click)
    monkeypatch.setattr(adapter, "_refresh_field", lambda field, path: refreshed)

    result = adapter._ensure_answer_value_slots(
        original,
        answer,
        "body > div#product-description",
    )

    assert len(result["controls"]) == 2
    assert clicks == [
        ("body > div#product-description", "sales_package_0_value")
    ]
    assert page.waits == [300]


def test_domain_does_not_click_plus_for_single_value(monkeypatch):
    adapter = MakroDomainAdapter(FakePage())
    original = _field(1)
    answer = SimpleNamespace(answer_values=["Camera"])

    def unexpected(*args, **kwargs):
        raise AssertionError("single value must not touch +")

    monkeypatch.setattr("app.makro.domain.click_add_value_for_control", unexpected)

    assert adapter._ensure_answer_value_slots(original, answer, "section") is original
