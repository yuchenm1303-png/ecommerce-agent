"""Focused tests for Makro Step 3 section readiness."""

from __future__ import annotations

from app.makro import sections


class _AsyncSectionPage:
    def __init__(self) -> None:
        self.wait_calls: list[int] = []

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_calls.append(milliseconds)


def test_section_readiness_reacquires_replaced_card_until_fields_render(
    monkeypatch,
) -> None:
    page = _AsyncSectionPage()
    rendered_sections = iter(
        [
            {"path": "body > collapsed-card", "has_cancel": True, "has_fields": False},
            {"path": "body > expanded-card", "has_cancel": True, "has_fields": False},
            {"path": "body > expanded-card", "has_cancel": True, "has_fields": True},
        ]
    )
    observed: list[tuple[object, str]] = []

    def fake_find_section(current_page, title: str):
        observed.append((current_page, title))
        return next(rendered_sections)

    monkeypatch.setattr(sections, "find_section", fake_find_section)

    ready = sections._wait_for_section_fields(
        page,
        "Product Description",
        wait_ms=25,
        timeout_s=1.0,
    )

    assert ready == {
        "path": "body > expanded-card",
        "has_cancel": True,
        "has_fields": True,
    }
    assert observed == [(page, "Product Description")] * 3
    assert page.wait_calls == [25, 25]
