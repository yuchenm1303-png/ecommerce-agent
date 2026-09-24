from __future__ import annotations

from app.makro.taxonomy_navigation import navigate_live_taxonomy


class ProgressiveTaxonomyPage:
    """Model Makro painting one child row before the rest of the column."""

    def __init__(self) -> None:
        self.selected: dict[int, str] = {}
        self.topwear_reads = 0
        self.leaf = False
        self.clicks: list[tuple[int, str]] = []

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def columns(self) -> list[list[str]]:
        columns: list[list[str]] = [["Clothing", "Home & Kitchen"]]
        if self.selected.get(0) == "Clothing":
            columns.append(["Topwear", "Sportswear & Gymwear"])
            if self.selected.get(1) == "Topwear":
                self.topwear_reads += 1
                if self.topwear_reads <= 2:
                    columns.append(["Raincoat"])
                else:
                    columns.append(["Raincoat", "Blouse", "Tops"])
        return columns

    def click(self, level: int, node: str) -> bool:
        self.clicks.append((level, node))
        self.selected[level] = node
        for stale_level in list(self.selected):
            if stale_level > level:
                del self.selected[stale_level]
        self.leaf = level == 2 and node == "Tops"
        return True


def test_taxonomy_waits_for_incremental_child_column_before_semantic_choice() -> None:
    page = ProgressiveTaxonomyPage()
    decisions: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def choose(path: list[str], candidates: list[str]) -> str:
        decisions.append((tuple(path), tuple(candidates)))
        if path == []:
            return "Clothing"
        if path == ["Clothing"]:
            return "Topwear"
        if path == ["Clothing", "Topwear"]:
            assert candidates == ["Raincoat", "Blouse", "Tops"]
            return "Tops"
        raise AssertionError((path, candidates))

    selected = navigate_live_taxonomy(
        page,
        columns_fn=page.columns,
        click_fn=page.click,
        choose_fn=choose,
        leaf_ready_fn=lambda node: page.leaf and page.selected.get(2) == node,
        complete_leaf_fn=lambda node: node,
        wait_ms=800,
        transition_polls=12,
    )

    assert selected == "Tops"
    assert (("Clothing", "Topwear"), ("Raincoat",)) not in decisions
    assert page.clicks == [
        (0, "Clothing"),
        (1, "Topwear"),
        (2, "Tops"),
    ]
