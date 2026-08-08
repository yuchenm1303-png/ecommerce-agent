from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .qa_catalog import QuestionCatalog, QuestionRecord
from .source_bundle import normalize_key


MATCHED = "matched"
UNMATCHED = "unmatched"
AMBIGUOUS = "ambiguous"


@dataclass(slots=True)
class QuestionFieldMatch:
    question: QuestionRecord
    status: str
    semantic_field: dict[str, Any] | None = None
    match_basis: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        field = self.semantic_field or {}
        return {
            "question_number": self.question.number,
            "question": self.question.question,
            "status": self.status,
            "match_basis": self.match_basis,
            "detail": self.detail,
            "attribute_key": str(field.get("attribute_key") or ""),
            "field_label": str(field.get("label") or ""),
            "section_heading": str(field.get("section_heading") or ""),
        }


@dataclass(slots=True)
class MatchAudit:
    matches: list[QuestionFieldMatch] = field(default_factory=list)
    unmatched_fields: list[dict[str, Any]] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return sum(1 for item in self.matches if item.status == MATCHED)

    @property
    def ambiguous_count(self) -> int:
        return sum(1 for item in self.matches if item.status == AMBIGUOUS)

    @property
    def unmatched_question_count(self) -> int:
        return sum(1 for item in self.matches if item.status == UNMATCHED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "matched_count": self.matched_count,
            "ambiguous_count": self.ambiguous_count,
            "unmatched_question_count": self.unmatched_question_count,
            "unmatched_field_count": len(self.unmatched_fields),
            "matches": [item.as_dict() for item in self.matches],
            "unmatched_fields": [
                {
                    "attribute_key": str(field.get("attribute_key") or ""),
                    "label": str(field.get("label") or ""),
                    "section_heading": str(field.get("section_heading") or ""),
                }
                for field in self.unmatched_fields
            ],
        }


def _field_keys(field: dict[str, Any]) -> set[str]:
    return {
        value
        for value in (
            normalize_key(field.get("attribute_key")),
            normalize_key(field.get("label")),
        )
        if value
    }


def _question_keys(question: QuestionRecord, aliases: dict[str, tuple[str, ...]]) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    direct = normalize_key(question.question)
    if direct:
        output.append((direct, "exact-normalized"))
    for alias in aliases.get(direct, ()):
        normalized = normalize_key(alias)
        if normalized and normalized != direct:
            output.append((normalized, "explicit-alias"))
    return output


def _section_key(value: object) -> str:
    """Normalize a QA category/live heading while removing UI-only counters.

    Makro headings contain decorations such as ``(Optional)`` and ``(0/46)``.
    Customer QA categories normally contain only the semantic section name.
    These decorations must not turn an otherwise exact section match into an
    ambiguity with fields from Price/Stock/Shipping or another section.
    """

    text = str(value or "").strip()
    text = re.sub(r"\([^)]*\)", " ", text)
    return normalize_key(text)


def _filter_candidates_by_question_section(
    question: QuestionRecord,
    candidates: list[int],
    fields: list[dict[str, Any]],
) -> list[int]:
    wanted = _section_key(question.category)
    if not wanted or not candidates:
        return candidates

    # A non-empty QA category is authoritative metadata. If live candidates
    # expose section headings, never fall back to a same-named field in another
    # section merely because its label/attribute_key matches.
    candidates_with_section = [
        index
        for index in candidates
        if _section_key(fields[index].get("section_heading"))
    ]
    if not candidates_with_section:
        return candidates
    return [
        index
        for index in candidates_with_section
        if _section_key(fields[index].get("section_heading")) == wanted
    ]


def match_questions_to_fields(
    catalog: QuestionCatalog,
    semantic_fields: Iterable[dict[str, Any]],
    *,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> MatchAudit:
    """One-to-one deterministic matcher between customer questions and live Makro fields.

    No fuzzy similarity is used. A question matches only an exact normalized
    label/attribute key or an explicit alias supplied by configuration. When the
    QA row carries a category/section, candidates must also belong to that live
    section. This prevents generic names such as Height/Length from crossing
    Product/Additional Description into Price, Stock and Shipping fields.
    """

    alias_map = aliases or {}
    fields = list(semantic_fields)
    keys_by_index = [_field_keys(field) for field in fields]
    used: set[int] = set()
    matches: list[QuestionFieldMatch] = []

    for question in catalog.questions:
        resolved: list[tuple[int, str]] = []
        for key, basis in _question_keys(question, alias_map):
            candidates = [
                index
                for index, field_keys in enumerate(keys_by_index)
                if index not in used and key in field_keys
            ]
            candidates = _filter_candidates_by_question_section(question, candidates, fields)
            if candidates:
                resolved.extend((index, basis) for index in candidates)
                # Direct exact-normalized match has precedence over aliases.
                if basis == "exact-normalized":
                    break

        unique_indexes = sorted({index for index, _ in resolved})
        if not unique_indexes:
            section_detail = (
                f" QA category={question.category!r} 也必须与 live section 一致。"
                if question.category
                else ""
            )
            matches.append(
                QuestionFieldMatch(
                    question=question,
                    status=UNMATCHED,
                    detail=(
                        "没有找到 exact-normalized 或 explicit-alias 的唯一 Makro 字段。"
                        + section_detail
                    ),
                )
            )
            continue
        if len(unique_indexes) > 1:
            labels = [
                str(fields[index].get("label") or fields[index].get("attribute_key") or "")
                + (
                    f" [{fields[index].get('section_heading')}]"
                    if fields[index].get("section_heading")
                    else ""
                )
                for index in unique_indexes
            ]
            matches.append(
                QuestionFieldMatch(
                    question=question,
                    status=AMBIGUOUS,
                    detail="同一问题匹配多个字段：" + " | ".join(labels),
                )
            )
            continue

        index = unique_indexes[0]
        basis = next(basis for candidate, basis in resolved if candidate == index)
        used.add(index)
        matches.append(
            QuestionFieldMatch(
                question=question,
                status=MATCHED,
                semantic_field=fields[index],
                match_basis=basis,
                detail="唯一确定性匹配。",
            )
        )

    return MatchAudit(
        matches=matches,
        unmatched_fields=[field for index, field in enumerate(fields) if index not in used],
    )
