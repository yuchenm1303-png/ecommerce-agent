"""Focused tests for Makro Step 3 section readiness."""

from __future__ import annotations

from app.makro.sections import _wait_for_section_fields


class _AsyncSectionPage:
    def __init__(self) -> None:
        self.states = [
            {"found": True, "has_cancel": True, "has_fields": False},
            {"found": True, "has_cancel": True, "has_fields": False},
            {"found": True, "has_cancel": True, "has_fields": True},
        ]
        self.evaluate_calls = 0
        self.wait_calls: list[int] = []

    def evaluate(self, _script, _payload):
        index = min(self.evaluate_calls, len(self.states) - 1)
        self.evaluate_calls += 1
        return self.states[index]

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_calls.append(milliseconds)


def test_section_readiness_waits_for_fields_after_cancel_appears() -> None:
    page = _AsyncSectionPage()

    assert (
        _wait_for_section_fields(
            page,
            "body > section",
            wait_ms=25,
            timeout_s=1.0,
        )
        is True
    )
    assert page.evaluate_calls == 3
    assert page.wait_calls == [25, 25]
