from __future__ import annotations

import app.makro.vertical_selection as vertical_selection
from app.makro.listing_creation import ListingBootstrapHints


class FakePage:
    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class FakeTaxonomy:
    def __init__(self, columns: list[list[str]]) -> None:
        self._columns = [list(column) for column in columns]
        self.clicks: list[tuple[int, str]] = []

    def columns(self) -> list[list[str]]:
        return [list(column) for column in self._columns]

    def click_node(self, level: int, text: str) -> bool:
        self.clicks.append((level, text))
        return True


def _hints() -> ListingBootstrapHints:
    return ListingBootstrapHints(
        vertical_search_terms=("neck massager",),
        brand="",
        brand_status="unknown",
        product_summary="portable rechargeable neck and shoulder massager",
        product_identity={"product_type_en": "neck massager"},
    )


def test_partial_taxonomy_recovery_starts_at_deepest_visible_column(monkeypatch) -> None:
    columns = [
        ["Furniture", "Gaming", "Health & Beauty", "Home & Kitchen"],
        ["Bath and Spa", "Health Care Devices", "Personal Care & Grooming"],
    ]
    taxonomy = FakeTaxonomy(columns)
    seen_columns: list[list[list[str]]] = []

    def fake_navigate(_page, **kwargs):
        shifted = kwargs["columns_fn"]()
        seen_columns.append(shifted)
        assert shifted == [columns[1]]
        assert kwargs["click_fn"](0, "Health Care Devices") is True
        return "massage_device"

    monkeypatch.setattr(vertical_selection, "navigate_live_taxonomy", fake_navigate)
    result = vertical_selection._resume_partial_taxonomy(
        FakePage(), object(), _hints(), taxonomy, columns, wait_ms=0
    )
    assert result == "massage_device"
    assert seen_columns == [[columns[1]]]
    assert taxonomy.clicks == [(1, "Health Care Devices")]


def test_partial_taxonomy_recovery_moves_outward(monkeypatch) -> None:
    columns = [
        ["Furniture", "Gaming", "Health & Beauty", "Home & Kitchen"],
        ["Bath and Spa", "Health Care Devices", "Personal Care & Grooming"],
    ]
    taxonomy = FakeTaxonomy(columns)
    calls: list[list[list[str]]] = []

    def fake_navigate(_page, **kwargs):
        shifted = kwargs["columns_fn"]()
        calls.append(shifted)
        if len(calls) == 1:
            return ""
        assert kwargs["click_fn"](0, "Health & Beauty") is True
        return "massage_device"

    monkeypatch.setattr(vertical_selection, "navigate_live_taxonomy", fake_navigate)
    result = vertical_selection._resume_partial_taxonomy(
        FakePage(), object(), _hints(), taxonomy, columns, wait_ms=0
    )
    assert result == "massage_device"
    assert calls == [[columns[1]], columns]
    assert taxonomy.clicks == [(0, "Health & Beauty")]


def test_select_vertical_prefers_grounded_search_before_taxonomy(monkeypatch) -> None:
    page = FakePage()
    hints = _hints()
    monkeypatch.setattr(vertical_selection, "_committed_vertical_from_later_stage", lambda _page: "")
    monkeypatch.setattr(vertical_selection, "is_vertical_interaction_ready", lambda _page: True)
    monkeypatch.setattr(
        vertical_selection,
        "_try_select_via_search",
        lambda *_args, **_kwargs: ("neck_massager", ["Health / Neck Massager"]),
    )

    def taxonomy_must_not_run(_page):
        raise AssertionError("taxonomy must not mutate before successful grounded search")

    monkeypatch.setattr(vertical_selection, "ResilientMakroTaxonomyBrowser", taxonomy_must_not_run)
    assert vertical_selection.select_vertical(page, object(), hints, wait_ms=0) == "neck_massager"


def test_select_vertical_falls_back_to_structural_taxonomy_when_search_has_no_candidate(monkeypatch) -> None:
    page = FakePage()
    hints = _hints()
    taxonomy = FakeTaxonomy([["Health & Beauty"]])
    monkeypatch.setattr(vertical_selection, "_committed_vertical_from_later_stage", lambda _page: "")
    monkeypatch.setattr(vertical_selection, "is_vertical_interaction_ready", lambda _page: True)
    monkeypatch.setattr(vertical_selection, "_try_select_via_search", lambda *_args, **_kwargs: ("", []))
    monkeypatch.setattr(vertical_selection, "ResilientMakroTaxonomyBrowser", lambda _page: taxonomy)
    monkeypatch.setattr(vertical_selection, "_select_via_taxonomy", lambda *_args, **_kwargs: "neck_massager")
    assert vertical_selection.select_vertical(page, object(), hints, wait_ms=0) == "neck_massager"
