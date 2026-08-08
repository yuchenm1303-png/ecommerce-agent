from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .qa_catalog import QuestionCatalog, QuestionRecord
from .source_bundle import normalize_key

SCHEMA_VERSION = 1


def _stable_section(value: object) -> str:
    text = str(value or "").strip()
    # Makro completion counters and '(Optional)' are presentation state, not
    # schema identity. Ignore them so (0/14) -> (5/14) after Save is not drift.
    text = re.sub(r"\([^)]*\)", " ", text)
    return normalize_key(text)


def _clean_options(items: Iterable[object]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in items:
        if isinstance(raw, dict):
            value = str(raw.get("text") or raw.get("value") or "").strip()
        else:
            value = str(raw or "").strip()
        key = normalize_key(value)
        if not value or not key or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return tuple(output)


def _field_options(field: dict[str, Any]) -> tuple[str, ...]:
    output = list(_clean_options(field.get("options") or []))
    seen = {normalize_key(item) for item in output}
    for control in field.get("controls") or []:
        name = str(control.get("name") or "")
        if name.endswith("_qualifier"):
            continue
        for item in _clean_options(control.get("options") or []):
            key = normalize_key(item)
            if key not in seen:
                output.append(item)
                seen.add(key)
    return tuple(output)


def _qualifier_options(field: dict[str, Any]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for control in field.get("controls") or []:
        if not str(control.get("name") or "").endswith("_qualifier"):
            continue
        for item in _clean_options(control.get("options") or []):
            key = normalize_key(item)
            if key not in seen:
                output.append(item)
                seen.add(key)
    return tuple(output)


def _schema_field(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "attribute_key": str(field.get("attribute_key") or ""),
        "label": str(field.get("label") or ""),
        "section_heading": str(field.get("section_heading") or ""),
        "required": bool(field.get("required")),
        "multi_value": bool(field.get("multi_value")),
        "options": list(_field_options(field)),
        "qualifier_options": list(_qualifier_options(field)),
        "help_text": str(field.get("help_text") or ""),
    }


def live_schema_payload(semantic_fields: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Serialize the live Makro question shape without values or sensitive state."""

    return {
        "schema_version": SCHEMA_VERSION,
        "fields": [_schema_field(field) for field in semantic_fields],
    }


def write_live_schema(
    semantic_fields: Iterable[dict[str, Any]],
    path: str | Path,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(live_schema_payload(semantic_fields), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def load_live_schema(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("live schema 格式或 schema_version 不受支持。")
    fields = payload.get("fields")
    if not isinstance(fields, list):
        raise ValueError("live schema 缺少 fields 数组。")
    return [item for item in fields if isinstance(item, dict)]


def _drift_signature(field: dict[str, Any]) -> tuple[object, ...]:
    return (
        normalize_key(field.get("attribute_key")),
        normalize_key(field.get("label")),
        _stable_section(field.get("section_heading")),
        bool(field.get("required")),
        bool(field.get("multi_value")),
        tuple(sorted(normalize_key(item) for item in _field_options(field))),
        tuple(sorted(normalize_key(item) for item in _qualifier_options(field))),
    )


def assert_live_schema_matches(
    planned_fields: Iterable[dict[str, Any]],
    current_fields: Iterable[dict[str, Any]],
) -> None:
    """Fail closed when the page question contract changed after planning.

    DOM paths, current values, completion counters and image/render state are
    deliberately ignored. Field identity, requiredness, multiplicity and option
    contracts must remain the same.
    """

    planned = Counter(_drift_signature(field) for field in planned_fields)
    current = Counter(_drift_signature(field) for field in current_fields)
    if planned == current:
        return

    removed = list((planned - current).elements())
    added = list((current - planned).elements())
    raise RuntimeError(
        "live schema 与当前 Makro 页面不一致；拒绝使用旧答案写入。"
        f" removed={removed[:8]!r}; added={added[:8]!r}"
    )


def augment_catalog_with_live_fields(
    catalog: QuestionCatalog,
    live_fields: Iterable[dict[str, Any]],
    *,
    business_locked: Callable[[str], bool],
) -> tuple[QuestionCatalog, list[str]]:
    """Add uniquely addressable non-business live fields missing from customer QA.

    Label is preferred because it represents the visible Makro question. A
    unique attribute_key is used only when the label collides with an existing
    QA question. A generic/reused attribute_key must never suppress a distinct
    visible label (real Makro has exhibited e.g. label=Length with key=height).
    """

    fields = list(live_fields)
    existing = {normalize_key(item.question) for item in catalog.questions}
    label_counts: Counter[str] = Counter(
        normalize_key(field.get("label")) for field in fields if normalize_key(field.get("label"))
    )
    key_counts: Counter[str] = Counter(
        normalize_key(field.get("attribute_key"))
        for field in fields
        if normalize_key(field.get("attribute_key"))
    )

    additions: list[QuestionRecord] = []
    warnings: list[str] = []
    seen_added: set[str] = set()

    for index, field in enumerate(fields, start=1):
        label = str(field.get("label") or "").strip()
        attribute_key = str(field.get("attribute_key") or "").strip()
        section = str(field.get("section_heading") or "").strip()
        label_key = normalize_key(label)
        attr_key = normalize_key(attribute_key)

        if business_locked(label) or business_locked(attribute_key):
            warnings.append(f"business_locked:{section}:{label or attribute_key}")
            continue

        # A visible label already present in customer QA is considered covered.
        # Do NOT let a reused generic attribute_key hide a different live label.
        if label_key and label_key in existing:
            if not (
                attr_key
                and attr_key not in existing
                and key_counts.get(attr_key, 0) == 1
                and attr_key not in seen_added
            ):
                continue

        question = ""
        basis = ""
        if (
            label_key
            and label_key not in existing
            and label_counts.get(label_key, 0) == 1
            and label_key not in seen_added
        ):
            question = label
            basis = "unique-live-label"
        elif (
            attr_key
            and attr_key not in existing
            and key_counts.get(attr_key, 0) == 1
            and attr_key not in seen_added
        ):
            question = attribute_key
            basis = "unique-live-attribute-key"

        if not question:
            warnings.append(f"no_unique_evidence_key:{section}:{label or attribute_key}")
            continue

        question_key = normalize_key(question)
        seen_added.add(question_key)
        options = _field_options(field)
        qualifier_options = _qualifier_options(field)
        explanation = (
            "Live Makro field not present in customer QA. "
            f"attribute_key={attribute_key}; label={label}; section={section}; "
            f"required={bool(field.get('required'))}; multi_value={bool(field.get('multi_value'))}. "
            "Answer only from exact product/package evidence; do not infer seller-controlled data."
        )
        additions.append(
            QuestionRecord(
                number=f"LIVE-{index}",
                question=question,
                explanation=explanation,
                category=section,
                options=options,
                unit=" | ".join(qualifier_options),
                source_reference=(
                    f"live-schema:section={_stable_section(section)}:"
                    f"attribute={attr_key or label_key}"
                ),
                row_number=-index,
                extra={
                    "origin": "live_makro_schema",
                    "attribute_key": attribute_key,
                    "live_label": label,
                    "match_basis": basis,
                },
            )
        )

    if not additions:
        return catalog, warnings
    return replace(catalog, questions=[*catalog.questions, *additions]), warnings
