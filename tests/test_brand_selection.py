from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.makro.brand_selection as brand_selection


class FakeProvider:
    name = "fake"

    def extract_json(self, request_payload):
        raise AssertionError("Step 2 brand confirmation must not call AI")


class FakeInput:
    def __init__(self) -> None:
        self.values: list[str] = []

    def fill(self, value: str) -> None:
        self.values.append(value)


class FakePage:
    def __init__(self) -> None:
        self.phase = "brand"
        self.ready_brand = ""
        self.waits: list[int] = []

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


def _hints(*, brand="Qigreesol", status="explicit"):
    return SimpleNamespace(brand=brand, brand_status=status)


def _install_common(monkeypatch, page, brand_input):
    monkeypatch.setattr(brand_selection, "is_brand_step", lambda current: current.phase == "brand")
    monkeypatch.setattr(brand_selection, "is_product_info_step", lambda current: current.phase == "product")
    monkeypatch.setattr(brand_selection, "_brand_input", lambda _page: brand_input)
    monkeypatch.setattr(brand_selection, "reconcile_portal_interruptions", lambda _page: 0)
    monkeypatch.setattr(
        brand_selection,
        "is_brand_ready_to_create_listing",
        lambda current, selected: current.ready_brand.casefold() == selected.casefold(),
    )
    monkeypatch.setattr(
        brand_selection,
        "is_brand_selected_confirmation",
        lambda _current, _selected: False,
    )
    monkeypatch.setattr(
        brand_selection,
        "_current_target_values",
        lambda current: ("solar_charge_controller", "QIGREESOL" if current.phase == "product" else ""),
    )
    monkeypatch.setattr(
        brand_selection,
        "_verify_selected_value",
        lambda _kind, selected, actual: actual or selected,
    )


def test_explicit_brand_uses_check_brand_confirmation_card(monkeypatch):
    page = FakePage()
    brand_input = FakeInput()
    _install_common(monkeypatch, page, brand_input)

    def check_brand(_page):
        page.ready_brand = "Qigreesol"

    def advance(_page, selected):
        assert selected == "Qigreesol"
        assert page.ready_brand == "Qigreesol"
        page.phase = "product"

    monkeypatch.setattr(brand_selection, "_click_check_brand", check_brand)
    monkeypatch.setattr(brand_selection, "_advance_brand_confirmation", advance)

    selected = brand_selection.select_brand(page, FakeProvider(), _hints(), wait_ms=0)

    assert selected == "QIGREESOL"
    assert brand_input.values == ["", "Qigreesol"]


def test_brand_confirmation_must_match_queried_brand(monkeypatch):
    page = FakePage()
    brand_input = FakeInput()
    _install_common(monkeypatch, page, brand_input)

    def check_brand(_page):
        page.ready_brand = "DifferentBrand"

    monkeypatch.setattr(brand_selection, "_click_check_brand", check_brand)
    monkeypatch.setattr(
        brand_selection,
        "_advance_brand_confirmation",
        lambda *_args: pytest.fail("mismatched confirmation card must never be clicked"),
    )

    with pytest.raises(RuntimeError, match="did not confirm"):
        brand_selection.select_brand(page, FakeProvider(), _hints(), wait_ms=0)


def test_unknown_supplier_brand_never_queries_portal(monkeypatch):
    page = FakePage()
    brand_input = FakeInput()
    _install_common(monkeypatch, page, brand_input)
    monkeypatch.setattr(brand_selection, "_brand_search_terms", lambda _hints: ())
    monkeypatch.setattr(
        brand_selection,
        "_click_check_brand",
        lambda _page: pytest.fail("unknown brand must not be queried"),
    )

    with pytest.raises(RuntimeError, match="did not establish a brand"):
        brand_selection.select_brand(
            page,
            FakeProvider(),
            _hints(brand="", status="unknown"),
            wait_ms=0,
        )


def test_brand_production_path_is_not_autocomplete_based() -> None:
    import inspect

    source = inspect.getsource(brand_selection.select_brand)
    module_source = inspect.getsource(brand_selection)
    assert "_click_check_brand(page)" in source
    assert "_wait_for_brand_check_outcome" in source
    assert "_advance_brand_confirmation(page, term)" in source
    assert "read_search_rows" not in module_source
    assert "click_search_row" not in module_source
    assert "begin_search_query" not in module_source
