from __future__ import annotations

from playwright.sync_api import Locator, Page

from .models import PageField


SUPPORTED_INPUT_TYPES = {"text", "number", "email", "url", "tel", "search"}


def _escape_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _selector_for_control(control: Locator) -> str | None:
    element_id = control.get_attribute("id")
    if element_id:
        return f'[id="{_escape_attr(element_id)}"]'

    name = control.get_attribute("name")
    if name:
        return f'[name="{_escape_attr(name)}"]'
    return None


def _control_type(control: Locator) -> str | None:
    tag_name = control.evaluate("el => el.tagName.toLowerCase()")
    if tag_name == "select":
        return "select"
    if tag_name == "textarea":
        return "text"
    if tag_name != "input":
        return None

    input_type = (control.get_attribute("type") or "text").lower()
    if input_type in SUPPORTED_INPUT_TYPES:
        return "text"
    if input_type == "checkbox":
        return "checkbox"
    return None


def extract_form_fields(page: Page) -> list[PageField]:
    """Discover ordinary form fields from their visible <label> elements.

    This intentionally uses conservative DOM relationships instead of AI guessing.
    Platform-specific extractors can be added later for complex seller backends.
    """

    fields: list[PageField] = []
    seen_selectors: set[str] = set()
    labels = page.locator("label")

    for index in range(labels.count()):
        label = labels.nth(index)
        text = label.inner_text().strip()
        if not text:
            continue

        control: Locator | None = None
        for_value = label.get_attribute("for")
        if for_value:
            candidate = page.locator(f'[id="{_escape_attr(for_value)}"]').first
            if candidate.count():
                control = candidate
        else:
            candidate = label.locator("input, select, textarea").first
            if candidate.count():
                control = candidate

        if control is None:
            continue

        selector = _selector_for_control(control)
        kind = _control_type(control)
        if not selector or not kind or selector in seen_selectors:
            continue

        seen_selectors.add(selector)
        fields.append(
            PageField(
                label=text,
                selector=selector,
                control_type=kind,
                required=control.get_attribute("required") is not None,
            )
        )

    return fields
