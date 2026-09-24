from __future__ import annotations

from app.browser_visual_hud import browser_visual_hud_reference
from app.makro.visual_execution_hud import _HUD_SRCDOC, _INSTALL_SCRIPT


class _FakeLocator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def evaluate(self, script: str, payload: dict) -> None:
        self.calls.append((script, payload))


def test_visual_hud_contains_noninteractive_light_reference_layer() -> None:
    assert "pointer-events:none" in _HUD_SRCDOC
    assert "reference-layer" in _HUD_SRCDOC
    assert "reference-card" in _HUD_SRCDOC
    assert "rgba(255,255,255,.96)" in _HUD_SRCDOC
    assert "pinReference" in _INSTALL_SCRIPT
    assert "references:new Map()" in _INSTALL_SCRIPT
    assert "USER DECISION" not in _INSTALL_SCRIPT


def test_browser_reference_card_passes_structured_rows_without_page_actions() -> None:
    locator = _FakeLocator()
    browser_visual_hud_reference(
        locator,
        {
            "kind": "price_reference",
            "key": "flipkart_selling_price",
            "eyebrow": "经营参考",
            "title": "Selling Price · 价格参考",
            "thought": "系统继续自动填写，不暂停等待。",
            "source": "Listing Studio · 当前经营参数",
            "warning": "",
            "rows": [
                {"label": "参考填写", "value": "R 5,000"},
                {"label": "Base Price / MRP 参考", "value": "R 6,000"},
                {"label": "价格结构", "value": "价差 R 1,000 · 16.7% 折扣"},
            ],
        },
    )

    assert len(locator.calls) == 1
    _script, payload = locator.calls[0]
    assert payload["id"] == "flipkart_selling_price"
    assert payload["kind"] == "price_reference"
    assert payload["eyebrow"] == "经营参考"
    assert payload["title"].startswith("Selling Price")
    assert payload["rows"][0] == {"label": "参考填写", "value": "R 5,000"}
    assert payload["rows"][2]["value"].endswith("16.7% 折扣")
