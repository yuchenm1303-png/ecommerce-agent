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
        self.presses: list[str] = []

    def fill(self, value: str) -> None:
        self.values.append(value)

    def press(self, key: str) -> None:
        self.presses.append(key)


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
        product_summary="solar charge controller",
        product_identity={"product_type_en": "solar charge controller", "brand": brand},
    )


def _install_browser_mechanics(monkeypatch, page, brand_input):
    monkeypatch.setattr(brand_selection, "is_brand_step", lambda current: current.phase == "brand")
    monkeypatch.setattr(brand_selection, "is_product_info_step", lambda current: current.phase == "product")
    monkeypatch.setattr(brand_selection, "_brand_input", lambda _page: brand_input)
    monkeypatch.setattr(brand_selection, "reconcile_portal_interruptions", lambda _page: 0)
    monkeypatch.setattr(brand_selection, "begin_search_query", lambda _input: None)
    monkeypatch.setattr(brand_selection, "_advance_brand_confirmation", lambda _page, _selected: None)
    monkeypatch.setattr(
        brand_selection,
        "_current_target_values",
        lambda _page: ("solar_charge_controller", "QIGREESOL"),
    )
    monkeypatch.setattr(
        brand_selection,
        "_verify_selected_value",
        lambda _kind, selected, actual: actual or selected,
    )


def test_step2_selects_only_query_owned_live_brand(monkeypatch):
    page = FakePage()
    brand_input = FakeInput()
    provider = FakeProvider()
    _install_browser_mechanics(monkeypatch, page, brand_input)

    rows = iter([[], ["Qigreesol", "Qigreesol Pro"]])
    monkeypatch.setattr(
        brand_selection,
        "read_search_rows",
        lambda _input: next(rows, ["Qigreesol", "Qigreesol Pro"]),
    )
    monkeypatch.setattr(brand_selection, "_click_check_brand", lambda _page: None)
    monkeypatch.setattr(
        brand_selection,
        "click_search_row",
        lambda _input, selected: selected == "Qigreesol",
    )

    selected = brand_selection.select_brand(page, provider, _hints(), wait_ms=0)
    assert selected == "QIGREESOL"
    assert "Qigreesol" in brand_input.values
    assert provider.requests == []


def test_unknown_supplier_brand_never_queries_or_chooses(monkeypatch):
    page = FakePage()
    brand_input = FakeInput()
    provider = FakeProvider({"selected_brand": "Samsung"})
    _install_browser_mechanics(monkeypatch, page, brand_input)
    monkeypatch.setattr(brand_selection, "_brand_search_terms", lambda _hints: ())

    with pytest.raises(RuntimeError, match="did not establish a brand"):
        brand_selection.select_brand(
            page,
            provider,
            _hints(brand="", status="unknown"),
            wait_ms=0,
        )
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


def test_brand_production_path_has_no_pagewide_candidate_scan() -> None:
    import inspect

    source = inspect.getsource(brand_selection.select_brand)
    assert "begin_search_query(brand_input)" in source
    assert "read_search_rows(brand_input)" in source
    assert "_visible_text_candidates" not in source
    assert "_click_exact_visible_text" not in source
