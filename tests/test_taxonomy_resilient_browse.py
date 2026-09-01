from __future__ import annotations

from app.makro import taxonomy_resilient as taxonomy_module


class _FakeSearch:
    def __init__(self) -> None:
        self.value = "plush dog toy"
        self.fills: list[str] = []

    def fill(self, value: str) -> None:
        self.value = value
        self.fills.append(value)

    def press(self, _key: str) -> None:
        return None

    def evaluate(self, _script: str) -> None:
        return None

    def bounding_box(self) -> dict[str, float]:
        return {"x": 40.0, "y": 260.0, "width": 840.0, "height": 44.0}


class _FakePage:
    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class _FakeOwnedTaxonomy:
    ROOT = [
        "Your Verticals",
        "Agricultural Products",
        "Arts & Crafts",
        "Automobile",
        "Pet Supplies",
        "Software",
    ]
    STALE = [
        ["Hair Combs"],
        ["Home Gym Combo"],
        ["Resistance Tubes"],
        ["Car Mobile Holder"],
    ]
    PET_CHILD = ["Aquarium Accessories", "Pet Gear", "Pet Grooming", "Pet Toys"]

    def __init__(self, _page: object) -> None:
        self.state = "root"
        self.clicked: list[tuple[int, str]] = []
        self.last_diagnostic = ""

    def columns(self, *, max_items_per_level: int = 160) -> list[list[str]]:
        del max_items_per_level
        if self.state == "root":
            return [list(self.ROOT), *[list(item) for item in self.STALE]]
        if self.state == "pet":
            # The real taxonomy child is deliberately inserted after two stale
            # Step-1 presentation columns. The resilient wrapper must bind the
            # newly-created child rather than trusting raw DOM column position.
            return [
                list(self.ROOT),
                list(self.STALE[0]),
                list(self.STALE[1]),
                list(self.PET_CHILD),
                list(self.STALE[2]),
                list(self.STALE[3]),
            ]
        return []

    def click_node(self, level: int, text: str, *, max_items_per_level: int = 160) -> bool:
        del max_items_per_level
        self.clicked.append((int(level), str(text)))
        if self.state == "root" and level == 0 and text == "Pet Supplies":
            self.state = "pet"
            return True
        if self.state == "pet" and level == 3 and text == "Pet Toys":
            return True
        return False


def _browser(monkeypatch):
    search = _FakeSearch()
    owned = _FakeOwnedTaxonomy(None)
    monkeypatch.setattr(taxonomy_module, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(taxonomy_module, "CatalogTaxonomyBrowser", lambda _page: owned)
    monkeypatch.setattr(
        taxonomy_module.ResilientMakroTaxonomyBrowser,
        "_harvest_physical_column",
        lambda self, raw_index, seed_values, *, max_items_per_level: list(seed_values),
    )
    monkeypatch.setattr(
        taxonomy_module.ResilientMakroTaxonomyBrowser,
        "_reveal_exact",
        lambda self, raw_index, wanted, *, max_items_per_level: True,
    )
    return taxonomy_module.ResilientMakroTaxonomyBrowser(_FakePage()), owned, search


def test_browse_fallback_resets_search_and_hides_preexisting_presentation_columns(monkeypatch) -> None:
    browser, _owned, search = _browser(monkeypatch)

    assert browser.columns() == [_FakeOwnedTaxonomy.ROOT]
    assert search.fills == [""]


def test_new_taxonomy_child_is_rebound_after_parent_click_without_trusting_raw_column_index(monkeypatch) -> None:
    browser, owned, _search = _browser(monkeypatch)

    assert browser.click_node(0, "Pet Supplies") is True
    assert browser.columns() == [_FakeOwnedTaxonomy.ROOT, _FakeOwnedTaxonomy.PET_CHILD]

    assert browser.click_node(1, "Pet Toys") is True
    assert owned.clicked == [(0, "Pet Supplies"), (3, "Pet Toys")]
