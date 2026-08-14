from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.makro.brand_selection as brand_selection


class FakeProvider:
    name = "fake"

    def __init__(self, response=None):
        self.response = response if response is not None else {"selected_brand": ""}
        self.requests = []

    def extract_json(self, request_payload):
        self.requests.append(request_payload)
        return self.response


class FakeInput:
    def __init__(self) -> None:
        self.values: list[str] = []

    def fill(self, value: str) -> None:
        self.values.append(value)


class FakePage:
    def __init__(self) -> None:
        self.phase = "brand"
        self.waits: list[int] = []

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


def _hints(*, brand="Qigreesol", status="explicit"):
    return SimpleNamespace(
        brand=brand,
        brand_status=status,
        product_summary="table lamp",
        product_identity={"product_type_en": "table lamp", "brand": brand},
    )


def _install_browser_mechanics(monkeypatch, page, brand_input):
    monkeypatch.setattr(brand_selection, "is_brand_step", lambda current: current.phase == "brand")
    monkeypatch.setattr(
        brand_selection,
        "is_product_info_step",
        lambda current: current.phase == "product",
    )
    monkeypatch.setattr(brand_selection, "_brand_input", lambda _page: brand_input)
    monkeypatch.setattr(brand_selection, "reconcile_portal_interruptions", lambda _page: 0)
    monkeypatch.setattr(
        brand_selection,
        "_click_exact_visible_text",
        lambda current, _selected: setattr(current, "phase", "product") or True,
    )
    monkeypatch.setattr(brand_selection, "_advance_brand_confirmation", lambda _page, _selected: None)
    monkeypatch.setattr(
        brand_selection,
        "_current_target_values",
        lambda _page: ("table_lamp", "QIGREESOL"),
    )
    monkeypatch.setattr(
        brand_selection,
        "_verify_selected_value",
        lambda _kind, selected, actual: actual or selected,
    )


def test_step2_consumes_current_live_brand_before_typing_supplier_brand(monkeypatch):
    page = FakePage()
    brand_input = FakeInput()
    provider = FakeProvider()
    _install_browser_mechanics(monkeypatch, page, brand_input)
    monkeypatch.setattr(
        brand_selection,
        "_live_brand_candidates",
        lambda _page: ["QIGREESOL", "Samsung"],
    )
    check_calls = []
    monkeypatch.setattr(brand_selection, "_click_check_brand", lambda _page: check_calls.append(True))

    selected = brand_selection.select_brand(page, provider, _hints(), wait_ms=0)

    assert selected == "QIGREESOL"
    assert brand_input.values == [""]
    assert check_calls == []
    assert provider.requests == []


def test_step2_uses_supplier_brand_only_as_bounded_discovery_fallback(monkeypatch):
    page = FakePage()
    brand_input = FakeInput()
    provider = FakeProvider({"selected_brand": ""})
    _install_browser_mechanics(monkeypatch, page, brand_input)

    def live_candidates(_page):
        if brand_input.values and brand_input.values[-1] == "Qigreesol":
            return ["Qigreesol", "Qigreesol Pro"]
        return ["Samsung", "Generic"]

    monkeypatch.setattr(brand_selection, "_live_brand_candidates", live_candidates)
    check_calls = []
    monkeypatch.setattr(brand_selection, "_click_check_brand", lambda _page: check_calls.append(True))

    selected = brand_selection.select_brand(page, provider, _hints(), wait_ms=0)

    assert selected == "QIGREESOL"
    assert brand_input.values == ["", "", "Qigreesol"]
    assert len(check_calls) == 1
    assert provider.requests[0]["context"]["discovery_query"] == ""


def test_unknown_supplier_brand_never_chooses_unrelated_live_brand(monkeypatch):
    page = FakePage()
    brand_input = FakeInput()
    provider = FakeProvider({"selected_brand": "Samsung"})
    _install_browser_mechanics(monkeypatch, page, brand_input)
    monkeypatch.setattr(
        brand_selection,
        "_live_brand_candidates",
        lambda _page: ["Samsung", "Generic"],
    )
    check_calls = []
    monkeypatch.setattr(brand_selection, "_click_check_brand", lambda _page: check_calls.append(True))

    with pytest.raises(RuntimeError, match="did not establish a brand"):
        brand_selection.select_brand(
            page,
            provider,
            _hints(brand="", status="unknown"),
            wait_ms=0,
        )

    assert check_calls == []
    assert provider.requests == []


def test_ai_brand_choice_must_be_exactly_one_live_candidate():
    provider = FakeProvider({"selected_brand": "InventedBrand"})
    hints = _hints(brand="Different", status="explicit")

    with pytest.raises(ValueError, match="not one unique live Makro candidate"):
        brand_selection.choose_live_brand_candidate(
            provider,
            hints,
            "",
            ["Samsung", "Generic"],
        )
