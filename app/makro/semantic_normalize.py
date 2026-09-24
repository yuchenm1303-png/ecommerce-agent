from __future__ import annotations

import copy
import re
from collections import defaultdict
from typing import Any


_INDEXED_NAME_RE = re.compile(r"_\d+_(?:value|display|name)?$")
_SECTION_COUNT_RE = re.compile(r"\s*\(\s*\d+\s*/\s*\d+\s*\)\s*$")
_READABLE_ATTRIBUTE_KEY_RE = re.compile(r"^[a-z]+(?:[_-][a-z]+)*$")
_RADIO_KINDS = {"radio", "custom_radio"}


def _section_identity(value: object) -> str:
    return _SECTION_COUNT_RE.sub("", str(value or "").strip()).casefold()


def _label_identity(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _humanize_attribute_key(value: object) -> str:
    """Return a safe human label only for transparent semantic attribute keys.

    DOM ids containing digits/camel-case/internal hashes are intentionally rejected;
    they are not trustworthy presentation labels. Simple runtime keys such as
    ``length``, ``breadth``, ``height`` and ``weight`` are stable enough to repair
    a duplicated rendered label without category-specific knowledge.
    """

    raw = str(value or "").strip()
    if not raw or raw != raw.casefold() or not _READABLE_ATTRIBUTE_KEY_RE.fullmatch(raw):
        return ""
    words = [word for word in re.split(r"[_-]+", raw) if word]
    return " ".join(word.capitalize() for word in words)


def _disambiguate_duplicate_labels(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair duplicate rendered labels only when stable attribute keys prove identity.

    Makro can render several distinct controls inside a shared attribute wrapper.
    The scanner may then expose the first visual label for every child control even
    though the DOM ids/names still carry distinct semantic keys. When a duplicate
    label occurs inside the same section/subsection, use those transparent keys as
    the canonical labels only if *every* member has a readable, unique key. Opaque
    keys fail closed and leave the rendered wording untouched.
    """

    grouped: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for index, field in enumerate(fields):
        label_key = _label_identity(field.get("label"))
        if not label_key:
            continue
        grouped[
            (
                _section_identity(field.get("section_heading")),
                _label_identity(field.get("subsection_heading")),
                label_key,
            )
        ].append(index)

    output = list(fields)
    for indices in grouped.values():
        if len(indices) < 2:
            continue
        canonical = [
            _humanize_attribute_key(fields[index].get("attribute_key"))
            for index in indices
        ]
        canonical_keys = {_label_identity(label) for label in canonical if label}
        if any(not label for label in canonical) or len(canonical_keys) != len(indices):
            continue

        rendered = str(fields[indices[0]].get("label") or "").strip()
        if all(_label_identity(label) == _label_identity(rendered) for label in canonical):
            continue

        for index, label in zip(indices, canonical):
            repaired = copy.copy(fields[index])
            repaired["rendered_label"] = str(fields[index].get("label") or "")
            repaired["label"] = label
            repaired["label_disambiguated_from_attribute_key"] = True
            output[index] = repaired
    return output


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
    """Return the canonical Makro semantic-field list after structural normalization.

    Native/custom radios are merged only when HTML declares the same non-empty
    group name inside one section/subsection. After that structural merge, any
    duplicated rendered labels are disambiguated only from transparent, unique DOM
    attribute keys. No product/category field list or value inference is used.
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
    return _disambiguate_duplicate_labels(output)


__all__ = ["coalesce_radio_semantic_fields"]
