from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .qa_catalog import QuestionCatalog, QuestionRecord
from .source_bundle import normalize_key

SCHEMA_VERSION = 1


def _field_options(field: dict[str, Any]) -> tuple[str, ...]:
    output: list[str] = []
    for option in field.get("options") or []:
        text = str(option.get("text") or option.get("value") or "").strip()
        if text and text not in output:
            output.append(text)
    for control in field.get("controls") or []:
        for option in control.get("options") or []:
            text = str(option.get("text") or option.get("value") or "").strip()
            if text and text not in output:
                output.append(text)
    return tuple(output)


def live_schema_payload(semantic_fields: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Serialize the live Makro question shape without values or sensitive state."""

    fields = []
    for field in semantic_fields:
        fields.append(
            {
                "attribute_key": str(field.get("attribute_key") or ""),
                "label": str(field.get("label") or ""),
                "section_heading": str(field.get("section_heading") or ""),
                "required": bool(field.get("required")),
                "options": list(_field_options(field)),
                "help_text": str(field.get("help_text") or ""),
            }
        )
    return {"schema_version": SCHEMA_VERSION, "fields": fields}


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


def augment_catalog_with_live_fields(
    catalog: QuestionCatalog,
    live_fields: Iterable[dict[str, Any]],
    *,
    business_locked: Callable[[str], bool],
) -> tuple[QuestionCatalog, list[str]]:
    """Add only uniquely addressable, non-business live fields missing from QA.

    The new question key must itself map directly back to the live field (label or
    attribute key). If neither is unique, the field is intentionally not exposed
    to AI; an explicit alias/section config is required instead.
    """

    fields = list(live_fields)
    existing = {normalize_key(item.question) for item in catalog.questions}
    label_counts: dict[str, int] = {}
    key_counts: dict[str, int] = {}
    for field in fields:
        label_counts[normalize_key(field.get("label"))] = label_counts.get(
            normalize_key(field.get("label")), 0
        ) + 1
        key_counts[normalize_key(field.get("attribute_key"))] = key_counts.get(
            normalize_key(field.get("attribute_key")), 0
        ) + 1

    additions: list[QuestionRecord] = []
    warnings: list[str] = []
    seen_added: set[str] = set()
    for index, field in enumerate(fields, start=1):
        label = str(field.get("label") or "").strip()
        attribute_key = str(field.get("attribute_key") or "").strip()
        section = str(field.get("section_heading") or "").strip()
        label_key = normalize_key(label)
        attr_key = normalize_key(attribute_key)

        if label_key in existing or attr_key in existing:
            continue
        if business_locked(label) or business_locked(attribute_key):
            warnings.append(f"business_locked:{section}:{label or attribute_key}")
            continue

        question = ""
        if label_key and label_counts.get(label_key) == 1:
            question = label
        elif attr_key and key_counts.get(attr_key) == 1:
            question = attribute_key
        if not question or normalize_key(question) in seen_added:
            warnings.append(f"no_unique_evidence_key:{section}:{label or attribute_key}")
            continue

        seen_added.add(normalize_key(question))
        options = tuple(
            str(item).strip()
            for item in field.get("options") or []
            if str(item).strip()
        )
        explanation = (
            "Live Makro field not present in customer QA. "
            f"attribute_key={attribute_key}; label={label}; section={section}; "
            f"required={bool(field.get('required'))}. "
            "Answer only from exact product/package evidence; do not infer seller-controlled data."
        )
        additions.append(
            QuestionRecord(
                number=f"LIVE-{index}",
                question=question,
                explanation=explanation,
                category=section,
                options=options,
                extra={
                    "origin": "live_makro_schema",
                    "attribute_key": attribute_key,
                    "live_label": label,
                },
            )
        )

    if not additions:
        return catalog, warnings
    return (
        replace(catalog, questions=[*catalog.questions, *additions]),
        warnings,
    )
