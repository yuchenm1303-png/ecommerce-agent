from __future__ import annotations

from types import SimpleNamespace

from app.makro import domain
from app.makro.domain import MakroDomainAdapter
from app.makro_dryrun import FillVerification


class _Page:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


def _field(marker: str) -> dict[str, object]:
    return {
        "attribute_key": "breadth",
        "label": "Breadth",
        "section_heading": "Price, Stock and Shipping Information",
        "controls": [{"name": f"breadth_{marker}_value", "field_kind": "input"}],
    }


def _answer() -> SimpleNamespace:
    return SimpleNamespace(
        status="resolved",
        answer="12.5",
        answer_values=["12.5"],
        qualifier="cm",
    )


def _verification(status: str, detail: str = "") -> FillVerification:
    return FillVerification(
        attribute_key="breadth",
        label="Breadth",
        status=status,
        expected=["12.5"],
        detail=detail,
        execution_family="numeric_qualified",
    )


def test_reactive_validation_rollback_rebinds_fresh_field_and_reapplies(monkeypatch) -> None:
    page = _Page()
    adapter = MakroDomainAdapter(page)  # type: ignore[arg-type]
    original = _field("old")
    refreshed = _field("fresh")
    calls: list[dict[str, object]] = []

    responses = iter([
        _verification("validation_failed", "React rolled value back after settled read"),
        _verification("validated"),
    ])

    def fake_fill(_page, semantic_field, answer, **_kwargs):
        calls.append(semantic_field)
        assert answer.answer == "12.5"
        return next(responses)

    monkeypatch.setattr(domain, "fill_resolved_field", fake_fill)
    monkeypatch.setattr(adapter, "_refresh_field", lambda _field, _path: refreshed)

    result = adapter.fill_resolved_field(
        original,
        _answer(),
        section_path="#price-stock",
        recheck_wait_ms=800,
    )

    assert result.status == "validated"
    assert calls == [original, refreshed]
    assert page.waits == [200]


def test_reactive_convergence_stays_bounded_when_live_state_never_accepts(monkeypatch) -> None:
    page = _Page()
    adapter = MakroDomainAdapter(page)  # type: ignore[arg-type]
    current = _field("0")
    refreshes = [_field("1"), _field("2")]
    calls = 0

    def fake_fill(_page, _field, _answer, **_kwargs):
        nonlocal calls
        calls += 1
        return _verification("validation_failed", f"rollback {calls}")

    monkeypatch.setattr(domain, "fill_resolved_field", fake_fill)
    monkeypatch.setattr(adapter, "_refresh_field", lambda _field, _path: refreshes.pop(0))

    result = adapter.fill_resolved_field(
        current,
        _answer(),
        section_path="#price-stock",
        recheck_wait_ms=800,
    )

    assert result.status == "validation_failed"
    assert calls == 3
    assert page.waits == [200, 200]
