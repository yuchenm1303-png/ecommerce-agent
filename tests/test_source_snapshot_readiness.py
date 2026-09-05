from __future__ import annotations

import pytest

from app.source_interaction import LOADING, ORDINARY_POPUP, PRODUCT_READY, SourcePageState
from app.source_snapshot import (
    SourceInteractionRequired,
    SourcePageNotReady,
    capture_page_snapshot,
)


class _FakePage:
    url = "https://supplier.example/product"

    def __init__(self) -> None:
        self.waits: list[int] = []
        self.extract_calls = 0

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(int(milliseconds))

    def evaluate(self, _script: str) -> dict[str, object]:
        self.extract_calls += 1
        return {
            "title": "Rendered product",
            "visible_text": "Rendered supplier product content",
            "table_rows": [],
            "json_ld": [],
            "embedded_data": [],
            "image_urls": [],
            "meta": {},
        }


def _state(value: str, reason: str = "observed state") -> SourcePageState:
    return SourcePageState(
        state=value,
        reason=reason,
        observed_url=_FakePage.url,
        title="Supplier",
        confidence=1.0,
        evidence_refs=("page-observation",),
    )


def test_transient_loading_is_reobserved_on_same_page(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage()
    decisions = iter((_state(LOADING, "SPA still hydrating"), _state(PRODUCT_READY, "ready")))

    monkeypatch.setattr(
        "app.source_interaction.classify_source_page_state",
        lambda *_args, **_kwargs: next(decisions),
    )

    snapshot = capture_page_snapshot(page, requested_url=page.url)

    assert page.waits == [2_000]
    assert page.extract_calls == 1
    assert snapshot.meta["page_state"] == PRODUCT_READY
    assert snapshot.visible_text == "Rendered supplier product content"


def test_transient_loading_exhausts_at_hard_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage()
    calls = 0

    def classify(*_args, **_kwargs) -> SourcePageState:
        nonlocal calls
        calls += 1
        return _state(LOADING, "still loading")

    monkeypatch.setattr("app.source_interaction.classify_source_page_state", classify)

    with pytest.raises(SourcePageNotReady, match=r"not capture-ready \(LOADING\)"):
        capture_page_snapshot(page, requested_url=page.url)

    assert calls == 4
    assert page.waits == [2_000, 2_000, 2_000]
    assert page.extract_calls == 0


def test_interaction_required_remains_immediate(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage()
    calls = 0

    def classify(*_args, **_kwargs) -> SourcePageState:
        nonlocal calls
        calls += 1
        return _state(ORDINARY_POPUP, "popup blocks product content")

    monkeypatch.setattr("app.source_interaction.classify_source_page_state", classify)

    with pytest.raises(SourceInteractionRequired):
        capture_page_snapshot(page, requested_url=page.url)

    assert calls == 1
    assert page.waits == []
    assert page.extract_calls == 0
