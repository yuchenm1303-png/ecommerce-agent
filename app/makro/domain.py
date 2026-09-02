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

from ..live_field_contract import execution_contract as canonical_execution_contract
from ..makro_dryrun import FillVerification, fill_resolved_field, verify_resolved_field
from .field_engine import execution_contract, fill_control as fill_live_control
from .fields import build_semantic_fields, scroll_and_capture
from .listing import (
    MakroListingTarget,
    assert_expected_vertical,
    is_makro_listing_page,
    parse_makro_listing_url,
    wait_for_authenticated_listing,
)
from .listing_draft_identity import DRAFT_IDENTITY_FIELD, listing_draft_identity_from_url
from .locators import click_add_value_for_control, selector_for_control
from .marketplace_constraints import _is_model_name_field, _strip_known_brand
from .photos import (
    PRODUCT_PHOTOS_SECTION,
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
from .unit_contract import fixed_rendered_unit


_PLAIN_TEXT_EXECUTION_FAMILIES = {
    "text",
    "text_multi",
    "long_text",
    "long_text_multi",
}


def _value_controls(field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control
        for control in field.get("controls") or []
        if control.get("field_kind") != "option"
        and not str(control.get("name") or "").endswith("_qualifier")
    ]


def _qualifier_controls(field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control
        for control in field.get("controls") or []
        if str(control.get("name") or "").endswith("_qualifier")
    ]


def _answer_values(answer: Any) -> list[str]:
    values = [
        str(value).strip()
        for value in list(getattr(answer, "answer_values", []) or [])
        if str(value).strip()
    ]
    if values:
        return values
    scalar = str(getattr(answer, "answer", "") or "").strip()
    return [scalar] if scalar else []


def _serialize_plain_text_qualifier(value: str, qualifier: str) -> str:
    """Serialize one approved value+qualifier pair for a plain text live control.

    The operation is deliberately mechanical: it preserves the AI-approved value
    and qualifier, merely rendering the two pieces into the single string shape
    required by a DOM control that exposes no separate qualifier contract.
    """

    text = str(value or "").strip()
    unit = str(qualifier or "").strip()
    if not text or not unit:
        return text

    folded_text = text.casefold()
    folded_unit = unit.casefold()
    if folded_text.endswith(folded_unit):
        prefix = text[: len(text) - len(unit)]
        if not prefix or prefix[-1].isspace() or prefix[-1].isdigit() or not prefix[-1].isalnum():
            return text
    return f"{text} {unit}"


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


def _execution_contract_key(field: dict[str, Any]) -> tuple[str, str, str]:
    """Stable semantic address for one Makro field within one adapter session."""
    return (
        str(field.get("attribute_key") or ""),
        str(field.get("label") or ""),
        base_section_title(str(field.get("section_heading") or "")),
    )


class MakroDomainAdapter:
    """Skill layer for the Makro Add a Single Listing page."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self._execution_contracts: dict[tuple[str, str, str], dict[str, Any]] = {}

    def _bind_execution_contracts(
        self,
        fields: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Freeze each field's mechanical contract on first observation.

        Later scans still return fresh DOM controls/selectors/current values, but
        the mechanical meaning of the field is rebound from this adapter-local
        snapshot. React validation text, rerenders and Save/reopen state therefore
        cannot reinterpret numeric/text/selection/unit semantics mid-run.
        """
        seen: set[tuple[str, str, str]] = set()
        for field in fields:
            key = _execution_contract_key(field)
            if key in seen:
                raise RuntimeError(
                    "当前 Makro 扫描出现无法唯一寻址的重复 semantic field；"
                    f"拒绝共享 execution contract：{key!r}"
                )
            seen.add(key)
            frozen = self._execution_contracts.get(key)
            if frozen is None:
                frozen = dict(canonical_execution_contract(field))
                self._execution_contracts[key] = frozen
            field["execution_contract"] = dict(frozen)
        return fields

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

    def cancel_product_photos(self, *, wait_ms: int = 450) -> None:
        """Cancel only the open Product Photos transaction.

        Photo orchestration owns an image-level transaction boundary and must not
        address that boundary through an arbitrary section title. Keep the DOM
        mechanics in the canonical section primitive while exposing an explicit
        domain operation for Product Photos recovery.
        """

        cancel_section(self.page, PRODUCT_PHOTOS_SECTION, wait_ms=wait_ms)

    def save_section(
        self,
        section_title: str,
        *,
        timeout_s: float | None = None,
    ) -> None:
        """Persist one section using the canonical section lifecycle policy.

        The sections layer owns the production default timeout because it also
        owns Makro's asynchronous Save/reopen verification contract. Callers may
        still supply an explicit timeout for a deliberate bounded override, but
        the adapter must not silently replace that canonical default.
        """

        if timeout_s is None:
            save_section(self.page, section_title)
            return
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
        fields = coalesce_radio_semantic_fields(build_semantic_fields(controls))
        try:
            identity = listing_draft_identity_from_url(self.page.url)
        except (ValueError, RuntimeError):
            identity = None
        if identity is not None:
            for field in fields:
                field[DRAFT_IDENTITY_FIELD] = dict(identity)
        return self._bind_execution_contracts(fields)

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

        AI owns product semantics. This boundary only projects an approved answer
        into the shape the current live control can mechanically execute. A plain
        text/long-text control has one value channel, so an approved qualifier is
        serialized into that value. Numeric fields, fixed-unit fields and real
        qualifier controls keep their strict unit contracts unchanged.

        The committed Brand lives in the current listing URL even when an older
        decision packet does not carry product_identity.brand. Makro rejects that
        exact Brand token inside Model Name, so remove it at the same final write
        and verification boundary. The original plan/answer object is never mutated.
        """

        constrained = answer
        qualifier = str(getattr(constrained, "qualifier", "") or "").strip()
        if qualifier:
            contract = execution_contract(semantic_field, constrained)
            if (
                contract.live_family in _PLAIN_TEXT_EXECUTION_FAMILIES
                and not fixed_rendered_unit(semantic_field)
            ):
                values = _answer_values(constrained)
                if values:
                    rendered = [
                        _serialize_plain_text_qualifier(value, qualifier)
                        for value in values
                    ]
                    projected = copy(constrained)
                    projected.answer_values = rendered
                    projected.answer = rendered[0]
                    projected.qualifier = None
                    detail = str(getattr(projected, "detail", "") or "").strip()
                    suffix = "Makro plain-text control serialized approved qualifier into value."
                    projected.detail = f"{detail} | {suffix}" if detail else suffix
                    constrained = projected
                    label = str(
                        semantic_field.get("label")
                        or semantic_field.get("attribute_key")
                        or "field"
                    ).replace("\t", " ").replace("\n", " ")
                    print(
                        f"GUI_EXEC_CONSTRAINT\t{label}\tqualifier_serialized\t{qualifier}",
                        flush=True,
                    )

        if not _is_model_name_field(semantic_field):
            return constrained
        target = self.current_target()
        brand = str((target.brand if target else None) or "").strip()
        if not brand:
            return constrained

        values = _answer_values(constrained)
        if not values:
            return constrained

        cleaned = [_strip_known_brand(value, brand) for value in values]
        if cleaned == values:
            return constrained
        meaningful = [value for value in cleaned if value]
        if not meaningful:
            raise RuntimeError(
                "Makro Model Name 去除当前 Brand 后为空；拒绝编造替代型号。"
            )

        projected = copy(constrained)
        projected.answer_values = meaningful
        projected.answer = meaningful[0]
        detail = str(getattr(projected, "detail", "") or "").strip()
        suffix = f"Makro Model Name removed committed Brand {brand!r}."
        projected.detail = f"{detail} | {suffix}" if detail else suffix
        print(
            f"GUI_EXEC_CONSTRAINT\tModel Name\tbrand_removed\t{brand}",
            flush=True,
        )
        return projected

    def _seed_repeatable_slot(
        self,
        semantic_field: dict[str, Any],
        answer: Any,
        slot_index: int,
        section_path: str,
    ) -> None:
        """Commit the approved value needed to enable Makro's disabled ``+``.

        Makro's repeatable control contract disables AddRemoveValueIcon while the
        current slot is empty. Slot materialisation is therefore inherently
        progressive: write the final approved value for the current last slot,
        let React enable ``+``, then request the next slot. The normal Generic
        Field Engine subsequently rewrites and verifies the complete answer, so
        this helper never invents or transforms product semantics.
        """

        values = list(getattr(answer, "answer_values", []) or [])
        controls = _value_controls(semantic_field)
        if slot_index < 0 or slot_index >= len(values) or slot_index >= len(controls):
            raise RuntimeError("multi-value slot seed index 与当前 live field 不一致。")
        fill_live_control(
            self.page,
            controls[slot_index],
            str(values[slot_index]),
            section_path=section_path,
        )

        qualifier = str(getattr(answer, "qualifier", "") or "").strip()
        if qualifier:
            qualifier_controls = _qualifier_controls(semantic_field)
            target: dict[str, Any] | None = None
            if len(qualifier_controls) == 1:
                target = qualifier_controls[0]
            elif slot_index < len(qualifier_controls):
                target = qualifier_controls[slot_index]
            if target is not None:
                fill_live_control(
                    self.page,
                    target,
                    qualifier,
                    section_path=section_path,
                )

    def _ensure_answer_value_slots(
        self,
        semantic_field: dict[str, Any],
        answer: Any,
        section_path: str | None,
    ) -> dict[str, Any]:
        """Materialise one live value slot per approved answer value.

        Some Makro repeatable attributes render exactly one empty slot plus a
        disabled ``+``. The scanner still treats that visible add control as
        repeatable capability. At execution time, if ``+`` is disabled, seed the
        current last slot with its final approved value, wait for React to enable
        the same control, then add the next slot. Every mutation boundary is
        re-scanned before the next DOM action so React-replaced controls are never
        reused from stale semantic metadata.
        """

        values = list(getattr(answer, "answer_values", []) or [])
        needed = len(values)
        current = semantic_field
        if needed <= len(_value_controls(current)) or needed <= 1:
            return current
        if not section_path:
            return current
        if not bool(current.get("has_add_value_control") or current.get("multi_value")):
            return current

        while len(_value_controls(current)) < needed:
            value_controls = _value_controls(current)
            if not value_controls:
                return current
            before = len(value_controls)
            anchor = value_controls[-1]
            click = click_add_value_for_control(
                self.page,
                section_path,
                anchor,
            )

            if (
                not click.get("clicked")
                and click.get("available")
                and click.get("reason") == "add-disabled"
            ):
                self._seed_repeatable_slot(
                    current,
                    answer,
                    before - 1,
                    section_path,
                )
                self.page.wait_for_timeout(180)

                current = self._refresh_field(current, section_path)
                value_controls = _value_controls(current)
                after_seed = len(value_controls)
                if after_seed > before:
                    continue
                if after_seed < before or not value_controls:
                    return current

                anchor = value_controls[-1]
                click = click_add_value_for_control(
                    self.page,
                    section_path,
                    anchor,
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
        """Write one approved field and converge across bounded React rerenders.

        Makro controls may acknowledge a value immediately and then remount/reset
        the controlled input on a later React commit. The low-level field engine
        deliberately detects that rollback. This adapter owns live-schema rebinding,
        so a detected rollback is resolved by rescanning the exact same semantic
        field and reapplying the exact same approved answer. No new value is inferred
        and a field that never converges remains a hard failure after three attempts.
        """

        section = base_section_title(str(semantic_field.get("section_heading") or ""))
        label = str(semantic_field.get("label") or semantic_field.get("attribute_key") or "field").strip()
        safe_section = section.replace("\t", " ").replace("\n", " ")
        safe_label = label.replace("\t", " ").replace("\n", " ")
        print(f"GUI_EXEC_FIELD\tSTART\t{safe_section}\t{safe_label}", flush=True)

        constrained_answer = self._constrained_execution_answer(semantic_field, answer)
        current_field = semantic_field
        max_attempts = 3 if section_path else 1
        verification: FillVerification | None = None

        for attempt in range(1, max_attempts + 1):
            expanded = self._ensure_answer_value_slots(
                current_field,
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
            if verification.status == "validated":
                break
            if attempt >= max_attempts or verification.status not in {
                "validation_failed",
                "fill_error",
            }:
                break

            settle_ms = max(150, min(400, max(1, int(recheck_wait_ms)) // 4))
            print(
                f"GUI_EXEC_FIELD\tRECONVERGE\t{safe_section}\t{safe_label}\t"
                f"attempt={attempt + 1}\tprior={verification.status}",
                flush=True,
            )
            self.page.wait_for_timeout(settle_ms)
            try:
                current_field = self._refresh_field(current_field, section_path)
            except Exception as exc:
                verification = FillVerification(
                    attribute_key=str(semantic_field.get("attribute_key") or ""),
                    label=label,
                    status="fill_error",
                    expected=list(getattr(constrained_answer, "answer_values", []) or []),
                    detail=f"React 状态回滚后重新绑定 live field 失败：{exc}",
                    execution_family=verification.execution_family,
                )
                break

        assert verification is not None
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
