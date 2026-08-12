from __future__ import annotations

import app.makro.vertical_selection as vertical_selection


class FakePage:
    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class FakeTaxonomy:
    def __init__(self) -> None:
        self.live_columns = [
            ["Gaming", "Hardware & Electricals", "Health & Beauty", "Home & Kitchen"],
            ["Bath and Spa", "Eye Care", "Health Care Devices", "Personal Care & Grooming"],
        ]
        self.clicks: list[tuple[int, str]] = []

    def columns(self) -> list[list[str]]:
        return [list(column) for column in self.live_columns]

    def click_node(self, level: int, text: str) -> bool:
        self.clicks.append((level, text))
        return True


class FakeSearch:
    def __init__(self) -> None:
        self.values: list[str] = []

    def fill(self, value: str) -> None:
        self.values.append(value)


def _hints():
    return vertical_selection.ListingBootstrapHints(
        vertical_search_terms=("neck massager",),
        brand="",
        brand_status="unknown",
        product_summary="portable rechargeable neck and shoulder massager",
        product_identity={"product_type_en": "neck massager"},
    )


def test_partial_taxonomy_resume_starts_from_deepest_visible_column(monkeypatch) -> None:
    page = FakePage()
    taxonomy = FakeTaxonomy()
    seen_columns: list[list[list[str]]] = []

    def fake_navigate(_page, **kwargs):
        columns = kwargs["columns_fn"]()
        seen_columns.append(columns)
        assert columns == [["Bath and Spa", "Eye Care", "Health Care Devices", "Personal Care & Grooming"]]
        assert kwargs["click_fn"](0, "Health Care Devices") is True
        return "massage_device"

    monkeypatch.setattr(vertical_selection, "navigate_live_taxonomy", fake_navigate)

    result = vertical_selection._resume_partial_taxonomy(
        page,
        object(),
        _hints(),
        taxonomy,
        taxonomy.columns(),
        wait_ms=800,
    )

    assert result == "massage_device"
    assert taxonomy.clicks == [(1, "Health Care Devices")]
    assert len(seen_columns) == 1


def test_partial_taxonomy_resume_backtracks_outward_when_deepest_branch_fails(monkeypatch) -> None:
    page = FakePage()
    taxonomy = FakeTaxonomy()
    starts: list[list[list[str]]] = []

    def fake_navigate(_page, **kwargs):
        columns = kwargs["columns_fn"]()
        starts.append(columns)
        if len(starts) == 1:
            assert columns == [["Bath and Spa", "Eye Care", "Health Care Devices", "Personal Care & Grooming"]]
            return ""
        assert columns[0] == ["Gaming", "Hardware & Electricals", "Health & Beauty", "Home & Kitchen"]
        assert kwargs["click_fn"](0, "Health & Beauty") is True
        return "massage_device"

    monkeypatch.setattr(vertical_selection, "navigate_live_taxonomy", fake_navigate)

    result = vertical_selection._resume_partial_taxonomy(
        page,
        object(),
        _hints(),
        taxonomy,
        taxonomy.columns(),
        wait_ms=800,
    )

    assert result == "massage_device"
    assert len(starts) == 2
    assert taxonomy.clicks == [(0, "Health & Beauty")]


def test_select_vertical_prefers_structural_resume_over_search_for_partial_state(monkeypatch) -> None:
    page = FakePage()
    taxonomy = FakeTaxonomy()
    search = FakeSearch()

    monkeypatch.setattr(vertical_selection, "is_vertical_interaction_ready", lambda _page: True)
    monkeypatch.setattr(vertical_selection, "ResilientMakroTaxonomyBrowser", lambda _page: taxonomy)
    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(
        vertical_selection,
        "_resume_partial_taxonomy",
        lambda *_args, **_kwargs: "massage_device",
    )

    def forbidden_search(*_args, **_kwargs):
        raise AssertionError("search fallback must not run when structural recovery succeeds")

    monkeypatch.setattr(vertical_selection, "_select_via_search_with_context", forbidden_search)

    result = vertical_selection.select_vertical(page, object(), _hints(), wait_ms=800)

    assert result == "massage_device"
    assert search.values == [""]
