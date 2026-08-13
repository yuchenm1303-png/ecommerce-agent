"""Makro domain adapter / skill layer facade.

The adapter is the single bridge between policy/orchestration and Makro DOM
behavior: listing guards, section lifecycle, semantic discovery, multi-value
expansion, real field fill/readback, photo persistence and post-Save checks.
"""

from __future__ import annotations

from copy import copy
from pathlib import Path
from typing import Any, Iterable

from playwright.sync_api import Page

from ..makro_dryrun import FillVerification, fill_resolved_field, verify_resolved_field
from .fields import build_semantic_fields, scroll_and_capture
from .listing import (
    MakroListingTarget,
    assert_expected_vertical,
    is_makro_listing_page,
    parse_makro_listing_url,
    wait_for_authenticated_listing,
)
from .locators import click_add_value_for_control, selector_for_control
from .marketplace_constraints import _is_model_name_field, _strip_known_brand
from .photos import (
    PhotoUploadResult,
    inspect_product_photos,
    upload_product_photos,
    verify_persisted_photo_count,
)
from .sections import (
    base_section_title,
    cancel_section,
    find_section,
    find_sections,
    open_section_for_edit,
    save_section,
    scan_section_fields,
    scan_sections,
    visible_section_errors,
)
from .semantic_normalize import coalesce_radio_semantic_fields


def _value_controls(field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control
        for control in field.get("controls") or []
        if control.get("field_kind") != "option"
        and not str(control.get("name") or "").endswith("_qualifier")
    ]


def _same_semantic_field(
    candidate: dict[str, Any],
    original: dict[str, Any],
) -> bool:
    return (
        str(candidate.get("attribute_key") or "")
        == str(original.get("attribute_key") or "")
        and str(candidate.get("label") or "") == str(original.get("label") or "")
        and base_section_title(str(candidate.get("section_heading") or ""))
        == base_section_title(str(original.get("section_heading") or ""))
    )


class MakroDomainAdapter:
    """Skill layer for the Makro Add a Single Listing page."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def is_listing_page(self) -> bool:
        return is_makro_listing_page(self.page)

    def current_target(self) -> MakroListingTarget | None:
        try:
            return parse_makro_listing_url(self.page.url)
        except ValueError:
            return None

    def assert_expected_vertical(self, expected_vertical: str | None) -> None:
        assert_expected_vertical(self.page, expected_vertical)

    def wait_for_authenticated_listing(
        self,
        initial_url: str | None = None,
        *,
        headless: bool = False,
        timeout_s: int = 30,
        navigate_first: bool = True,
    ) -> None:
        wait_for_authenticated_listing(
            self.page,
            initial_url,
            headless=headless,
            timeout_s=timeout_s,
            navigate_first=navigate_first,
        )

    def base_section_title(self, title: str) -> str:
        return base_section_title(title)

    def find_sections(self) -> list[dict[str, Any]]:
        return find_sections(self.page)

    def find_section(self, wanted: str) -> dict[str, Any] | None:
        return find_section(self.page, wanted)

    def open_section_for_edit(self, section: dict[str, Any]) -> None:
        open_section_for_edit(self.page, section)

    def cancel_section(self, section_title: str, *, wait_ms: int = 450) -> None:
        cancel_section(self.page, section_title, wait_ms=wait_ms)

    def save_section(self, section_title: str, *, timeout_s: float = 15.0) -> None:
        save_section(self.page, section_title, timeout_s=timeout_s)

    def visible_section_errors(self, section_path: str) -> list[str]:
        return visible_section_errors(self.page, section_path)

    def scan_sections(
        self,
        *,
        include_values: bool = False,
        wait_ms: int = 350,
        max_scroll_steps: int = 200,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        return scan_sections(
            self.page,
            include_values=include_values,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )

    def scan_section_fields(
        self,
        section_path: str,
        *,
        include_values: bool = False,
        wait_ms: int = 350,
        max_scroll_steps: int = 200,
    ) -> list[dict[str, Any]]:
        return scan_section_fields(
            self.page,
            section_path,
            include_values=include_values,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )

    def scroll_and_capture(
        self,
        *,
        include_values: bool = False,
        open_dropdowns: bool = False,
        wait_ms: int = 350,
        max_scroll_steps: int = 200,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return scroll_and_capture(
            self.page,
            include_values=include_values,
            open_dropdowns=open_dropdowns,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )

    def inspect_product_photos(self) -> dict[str, Any]:
        return inspect_product_photos(self.page)

    def upload_product_photos(
        self,
        image_paths: Iterable[str | Path],
        *,
        timeout_ms: int = 30_000,
    ) -> PhotoUploadResult:
        return upload_product_photos(
            self.page,
            image_paths,
            timeout_ms=timeout_ms,
        )

    def verify_persisted_photo_count(
        self,
        *,
        initial_count: int | None,
        expected_added: int,
    ) -> dict[str, Any]:
        return verify_persisted_photo_count(
            self.page,
            initial_count=initial_count,
            expected_added=expected_added,
        )

    def build_semantic_fields(self, controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return coalesce_radio_semantic_fields(build_semantic_fields(controls))

    def selector_for(self, control: dict[str, Any]) -> str:
        return selector_for_control(control)

    def _refresh_field(
        self,
        semantic_field: dict[str, Any],
        section_path: str,
    ) -> dict[str, Any]:
        controls = self.scan_section_fields(
            section_path,
            include_values=True,
            wait_ms=200,
            max_scroll_steps=200,
        )
        matches = [
            field
            for field in self.build_semantic_fields(controls)
            if _same_semantic_field(field, semantic_field)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"multi-value 刷新后字段匹配数={len(matches)}，期望 1："
                f"{semantic_field.get('label') or semantic_field.get('attribute_key')}"
            )
        return matches[0]

    def _constrained_execution_answer(
        self,
        semantic_field: dict[str, Any],
        answer: Any,
    ) -> Any:
        """Apply final DOM-known Makro mechanics without changing product semantics.

        The committed Brand lives in the current listing URL even when an older
        decision packet does not carry product_identity.brand. Makro rejects that
        exact Brand token inside Model Name, so remove it at the final write and
        verification boundary. The original plan/answer object is never mutated.
        """

        if not _is_model_name_field(semantic_field):
            return answer
        target = self.current_target()
        brand = str((target.brand if target else None) or "").strip()
        if not brand:
            return answer

        values = [
            str(value).strip()
            for value in list(getattr(answer, "answer_values", []) or [])
            if str(value).strip()
        ]
        if not values:
            scalar = str(getattr(answer, "answer", "") or "").strip()
            if scalar:
                values = [scalar]
        if not values:
            return answer

        cleaned = [_strip_known_brand(value, brand) for value in values]
        if cleaned == values:
            return answer
        meaningful = [value for value in cleaned if value]
        if not meaningful:
            raise RuntimeError(
                "Makro Model Name 去除当前 Brand 后为空；拒绝编造替代型号。"
            )

        constrained = copy(answer)
        constrained.answer_values = meaningful
        constrained.answer = meaningful[0]
        detail = str(getattr(constrained, "detail", "") or "").strip()
        suffix = f"Makro Model Name removed committed Brand {brand!r}."
        constrained.detail = f"{detail} | {suffix}" if detail else suffix
        print(
            f"GUI_EXEC_CONSTRAINT\tModel Name\tbrand_removed\t{brand}",
            flush=True,
        )
        return constrained

    def _ensure_answer_value_slots(
        self,
        semantic_field: dict[str, Any],
        answer: Any,
        section_path: str | None,
    ) -> dict[str, Any]:
        """Expand the live ``+`` control until every answer value has one slot.

        Expansion is attempted only when the answer actually contains more
        values than currently rendered controls and a section path is known. No
        synthetic value is written here; the field is re-discovered after every
        click so React-created indexed controls become first-class live controls.
        """

        needed = len(list(getattr(answer, "answer_values", []) or []))
        current = semantic_field
        if needed <= len(_value_controls(current)) or needed <= 1:
            return current
        if not section_path:
            return current

        while len(_value_controls(current)) < needed:
            value_controls = _value_controls(current)
            if not value_controls:
                return current
            before = len(value_controls)
            click = click_add_value_for_control(
                self.page,
                section_path,
                value_controls[0],
            )
            if not click.get("clicked"):
                return current
            self.page.wait_for_timeout(300)
            refreshed = self._refresh_field(current, section_path)
            after = len(_value_controls(refreshed))
            if after <= before:
                return refreshed
            current = refreshed
        return current

    def fill_resolved_field(
        self,
        semantic_field: dict[str, Any],
        answer: Any,
        *,
        section_path: str | None = None,
        recheck_wait_ms: int = 800,
    ) -> FillVerification:
        section = base_section_title(str(semantic_field.get("section_heading") or ""))
        label = str(semantic_field.get("label") or semantic_field.get("attribute_key") or "field").strip()
        safe_section = section.replace("\t", " ").replace("\n", " ")
        safe_label = label.replace("\t", " ").replace("\n", " ")
        print(f"GUI_EXEC_FIELD\tSTART\t{safe_section}\t{safe_label}", flush=True)
        constrained_answer = self._constrained_execution_answer(semantic_field, answer)
        expanded = self._ensure_answer_value_slots(
            semantic_field,
            constrained_answer,
            section_path,
        )
        verification = fill_resolved_field(
            self.page,
            expanded,
            constrained_answer,
            section_path=section_path,
            recheck_wait_ms=recheck_wait_ms,
        )
        print(
            f"GUI_EXEC_FIELD\tCOMPLETE\t{safe_section}\t{safe_label}\t"
            f"{verification.status}\t{verification.execution_family or 'unknown'}",
            flush=True,
        )
        return verification

    def verify_resolved_field(
        self,
        semantic_field: dict[str, Any],
        answer: Any,
        *,
        section_path: str | None = None,
    ) -> FillVerification:
        constrained_answer = self._constrained_execution_answer(semantic_field, answer)
        return verify_resolved_field(
            self.page,
            semantic_field,
            constrained_answer,
            section_path=section_path,
        )