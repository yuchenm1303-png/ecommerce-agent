from __future__ import annotations

import copy
import re
from collections import defaultdict
from typing import Any


_INDEXED_NAME_RE = re.compile(r"_\d+_(?:value|display|name)?$")
_SECTION_COUNT_RE = re.compile(r"\s*\(\s*\d+\s*/\s*\d+\s*\)\s*$")
_RADIO_KINDS = {"radio", "custom_radio"}


def _section_identity(value: object) -> str:
    return _SECTION_COUNT_RE.sub("", str(value or "").strip()).casefold()


def _radio_group_name(field: dict[str, Any]) -> str:
    controls = [
        control
        for control in field.get("controls") or []
        if str(control.get("field_kind") or "").casefold() in _RADIO_KINDS
    ]
    if not controls or len(controls) != len(field.get("controls") or []):
        return ""
    names = {str(control.get("name") or "").strip() for control in controls}
    names.discard("")
    if len(names) != 1:
        return ""
    name = next(iter(names))
    return _INDEXED_NAME_RE.sub("", name) or name


def _radio_option(control: dict[str, Any]) -> dict[str, Any] | None:
    text = str(control.get("label") or control.get("aria_label") or "").strip()
    value = str(control.get("value") or "").strip()
    if not text and not value:
        return None
    return {
        "text": text or value,
        "value": value or text,
        "selected": False,
        "disabled": bool(control.get("disabled")),
    }


def coalesce_radio_semantic_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge native/custom radios that HTML already declares as one named group.

    No label/category heuristics are used. A merge is allowed only when at least
    two radio-only semantic fields share the same non-empty ``name`` inside the
    same section/subsection. This repairs the scanner's normal id-first field
    identity for the one HTML control family where distinct ids intentionally
    belong to one semantic value.
    """

    grouped: dict[tuple[str, str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, field in enumerate(fields):
        group_name = _radio_group_name(field)
        if not group_name:
            continue
        key = (
            _section_identity(field.get("section_heading")),
            str(field.get("subsection_heading") or "").strip().casefold(),
            group_name,
        )
        grouped[key].append((index, field))

    replacements: dict[int, dict[str, Any]] = {}
    consumed: set[int] = set()
    for (_, _, group_name), members in grouped.items():
        if len(members) < 2:
            continue
        first_index, first = members[0]
        merged = copy.deepcopy(first)
        controls: list[dict[str, Any]] = []
        labels: list[str] = []
        options: list[dict[str, Any]] = []
        seen_options: set[tuple[str, str]] = set()
        required = False
        for index, field in members:
            consumed.add(index)
            required = required or bool(field.get("required"))
            label = str(field.get("label") or "").strip()
            if label and label not in labels:
                labels.append(label)
            for control in field.get("controls") or []:
                controls.append(copy.deepcopy(control))
                option = _radio_option(control)
                if option:
                    signature = (option["text"], option["value"])
                    if signature not in seen_options:
                        seen_options.add(signature)
                        options.append(option)

        merged["attribute_key"] = group_name
        merged["controls"] = controls
        merged["field_kind"] = "radio"
        merged["accepted_control_kinds"] = sorted(
            {
                str(control.get("field_kind") or "")
                for control in controls
                if control.get("field_kind")
            }
        )
        merged["required"] = required
        merged["multi_value"] = False
        merged["options"] = options
        if len(labels) == 1:
            merged["label"] = labels[0]
        elif not str(merged.get("label") or "").strip():
            merged["label"] = group_name.replace("_", " ").title()
        merged["radio_group_normalized"] = True
        replacements[first_index] = merged
        consumed.discard(first_index)

    output: list[dict[str, Any]] = []
    for index, field in enumerate(fields):
        if index in replacements:
            output.append(replacements[index])
        elif index not in consumed:
            output.append(field)
    return output


__all__ = ["coalesce_radio_semantic_fields"]
