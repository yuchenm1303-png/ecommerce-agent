from __future__ import annotations

import pytest

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


class _FakePage:
    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class _FakeOwnedTaxonomy:
    ROOT_CHUNKS = [
        ["Your Verticals", "Agricultural Products", "Arts & Crafts"],
        ["Automobile", "Pet Supplies", "Software"],
    ]
    PET_CHUNKS = [
        ["Aquarium Accessories", "Pet Gear"],
        ["Pet Grooming", "Pet Toys"],
    ]
    ARTS_CHUNKS = [["Painting Supplies", "Craft Kits"]]
    STALE = [
        ("card-hair", ["Hair Combs"]),
        ("card-gym", ["Home Gym Combo"]),
        ("card-resistance", ["Resistance Tubes"]),
        ("card-mobile", ["Car Mobile Holder"]),
    ]

    def __init__(self, _page: object, *, stuck_root: bool = False) -> None:
        self.state = "root"
        self.positions = {"root": 0, "pet": 0, "arts": 0}
        self.clicked: list[tuple[str, str]] = []
        self.last_diagnostic = ""
        self.stuck_root = bool(stuck_root)

    def _descriptor(
        self,
        group_id: str,
        chunks: list[list[str]],
        *,
        dom_order: int,
        is_root: bool = False,
    ) -> dict[str, object]:
        position = self.positions.get(group_id, 0)
        position = min(position, len(chunks) - 1)
        return {
            "group_id": group_id,
            "owner_id": group_id,
            "items": list(chunks[position]),
            "scrollable": len(chunks) > 1,
            "scroll_top": float(position * 100),
            "max_scroll": float((len(chunks) - 1) * 100),
            "client_height": 100.0,
            "dom_order": dom_order,
            "is_root": is_root,
        }

    def column_descriptors(self, *, max_items_per_level: int = 400) -> list[dict[str, object]]:
        del max_items_per_level
        output = [self._descriptor("root", self.ROOT_CHUNKS, dom_order=0, is_root=True)]
        for offset, (group_id, values) in enumerate(self.STALE, start=1):
            output.append(
                {
                    "group_id": group_id,
                    "owner_id": group_id,
                    "items": list(values),
                    "scrollable": False,
                    "scroll_top": 0.0,
                    "max_scroll": 0.0,
                    "client_height": 100.0,
                    "dom_order": offset,
                    "is_root": False,
                }
            )
        if self.state == "pet":
            # The true generated child appears structurally after two stale cards,
            # proving the wrapper cannot trust a raw numeric column index.
            child = self._descriptor("pet", self.PET_CHUNKS, dom_order=3)
            output.insert(3, child)
        elif self.state == "arts":
            child = self._descriptor("arts", self.ARTS_CHUNKS, dom_order=3)
            output.insert(3, child)
        if self.state in {"pet", "arts"}:
            # Keep deterministic DOM-order values after insertion.
            for index, entry in enumerate(output):
                entry["dom_order"] = index
        return output

    def scroll_owned_column(self, owner_id: str, action: str) -> dict[str, object]:
        if owner_id in {group_id for group_id, _ in self.STALE}:
            return {"found": True, "moved": False, "at_end": True, "scroll_top": 0.0, "max_scroll": 0.0}
        chunks = (
            self.ROOT_CHUNKS
            if owner_id == "root"
            else self.PET_CHUNKS
            if owner_id == "pet"
            else self.ARTS_CHUNKS
            if owner_id == "arts"
            else None
        )
        if chunks is None:
            raise RuntimeError(f"unknown owner {owner_id}")
        before = self.positions[owner_id]
        if action == "top":
            self.positions[owner_id] = 0
        elif action == "next":
            if owner_id == "root" and self.stuck_root and before < len(chunks) - 1:
                return {
                    "found": True,
                    "moved": False,
                    "at_end": False,
                    "scroll_top": float(before * 100),
                    "max_scroll": float((len(chunks) - 1) * 100),
                }
            self.positions[owner_id] = min(len(chunks) - 1, before + 1)
        else:
            raise RuntimeError(f"bad action {action}")
        after = self.positions[owner_id]
        return {
            "found": True,
            "moved": after != before,
            "at_end": after >= len(chunks) - 1,
            "scroll_top": float(after * 100),
            "max_scroll": float((len(chunks) - 1) * 100),
        }

    def click_owned_node(
        self,
        group_id: str,
        text: str,
        *,
        max_items_per_level: int = 400,
    ) -> bool:
        del max_items_per_level
        self.clicked.append((group_id, text))
        if group_id == "root" and text == "Pet Supplies" and self.positions["root"] == 1:
            self.state = "pet"
            self.positions["pet"] = 0
            return True
        if group_id == "root" and text == "Arts & Crafts" and self.positions["root"] == 0:
            self.state = "arts"
            self.positions["arts"] = 0
            return True
        if group_id == "pet" and text == "Pet Toys" and self.positions["pet"] == 1:
            return True
        if group_id == "arts" and text == "Craft Kits" and self.positions["arts"] == 0:
            return True
        return False


def _browser(monkeypatch, *, stuck_root: bool = False):
    search = _FakeSearch()
    owned = _FakeOwnedTaxonomy(None, stuck_root=stuck_root)
    monkeypatch.setattr(taxonomy_module, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(taxonomy_module, "CatalogTaxonomyBrowser", lambda _page: owned)
    return taxonomy_module.ResilientMakroTaxonomyBrowser(_FakePage()), owned, search


def test_browse_fallback_harvests_offscreen_root_and_resets_search(monkeypatch) -> None:
    browser, _owned, search = _browser(monkeypatch)

    assert browser.columns() == [[
        "Your Verticals",
        "Agricultural Products",
        "Arts & Crafts",
        "Automobile",
        "Pet Supplies",
        "Software",
    ]]
    assert search.fills == [""]


def test_generated_child_is_rebound_structurally_and_fully_scrolled(monkeypatch) -> None:
    browser, owned, _search = _browser(monkeypatch)

    assert browser.click_node(0, "Pet Supplies") is True
    assert browser.columns() == [
        [
            "Your Verticals",
            "Agricultural Products",
            "Arts & Crafts",
            "Automobile",
            "Pet Supplies",
            "Software",
        ],
        ["Aquarium Accessories", "Pet Gear", "Pet Grooming", "Pet Toys"],
    ]
    assert browser.click_node(1, "Pet Toys") is True
    assert owned.clicked == [("root", "Pet Supplies"), ("pet", "Pet Toys")]


def test_shallow_sibling_click_invalidates_deeper_branch_state(monkeypatch) -> None:
    browser, owned, _search = _browser(monkeypatch)

    assert browser.click_node(0, "Pet Supplies") is True
    assert browser.click_node(1, "Pet Toys") is True
    assert browser._clicked_depth == 1
    assert set(browser._stale_after_parent) == {1, 2}

    assert browser.click_node(0, "Arts & Crafts") is True

    assert browser._clicked_depth == 0
    assert set(browser._stale_after_parent) == {1}
    assert browser.columns() == [
        [
            "Your Verticals",
            "Agricultural Products",
            "Arts & Crafts",
            "Automobile",
            "Pet Supplies",
            "Software",
        ],
        ["Painting Supplies", "Craft Kits"],
    ]
    assert browser.click_node(1, "Craft Kits") is True
    assert owned.clicked == [
        ("root", "Pet Supplies"),
        ("pet", "Pet Toys"),
        ("root", "Arts & Crafts"),
        ("arts", "Craft Kits"),
    ]


def test_partial_scroll_can_never_masquerade_as_complete_taxonomy(monkeypatch) -> None:
    browser, _owned, _search = _browser(monkeypatch, stuck_root=True)

    with pytest.raises(taxonomy_module.TaxonomyMechanicalError, match="could not advance"):
        browser.columns()
