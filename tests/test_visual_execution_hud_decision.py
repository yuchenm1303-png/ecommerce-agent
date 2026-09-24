from __future__ import annotations

from app.browser_visual_hud import browser_visual_hud_advice
from app.makro.visual_execution_hud import _HUD_SRCDOC, _INSTALL_SCRIPT


class _FakeLocator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def evaluate(self, script: str, payload: dict) -> None:
        self.calls.append((script, payload))


def test_visual_hud_contains_noninteractive_decision_panel() -> None:
    assert "pointer-events:none" in _HUD_SRCDOC
    assert "advice-panel" in _HUD_SRCDOC
    assert "advice-mode" in _HUD_SRCDOC
    assert "USER DECISION" in _INSTALL_SCRIPT
    assert "adviceUntil" in _INSTALL_SCRIPT


def test_browser_hud_advice_passes_structured_rows_without_page_actions() -> None:
    locator = _FakeLocator()
    browser_visual_hud_advice(
        locator,
        {
            "kind": "price_decision",
            "title": "Selling Price · 需要你决定",
            "thought": "只显示建议，不替用户定价。",
            "source": "用户决策 + Makro 经营约束",
            "warning": "",
            "rows": [
                {"label": "当前确认", "value": "R 1,499"},
                {"label": "自动占位", "value": "已禁用"},
            ],
        },
    )

    assert len(locator.calls) == 1
    _script, payload = locator.calls[0]
    assert payload["kind"] == "price_decision"
    assert payload["title"].startswith("Selling Price")
    assert payload["rows"][0] == {"label": "当前确认", "value": "R 1,499"}
    assert payload["rows"][1]["value"] == "已禁用"
