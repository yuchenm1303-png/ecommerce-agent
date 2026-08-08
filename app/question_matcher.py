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
    text = str(value or "").strip()
    text = re.sub(r"\([^)]*\)", " ", text)
    return normalize_key(text)


_ANSWER_FORM_CATEGORY_PATTERNS = (
    re.compile(r"^(?:单项|多项)?(?:填空|选择|输入)$"),
    re.compile(r"^(?:单选|多选|文本|数字|数值|下拉|下拉选择|自由文本)$"),
    re.compile(r"^(?:single|multi|multiple)?(?:choice|select|selection|input|text|number|numeric)$"),
    re.compile(r"^(?:singlechoice|multiplechoice|multichoice|singleselect|multiselect|textinput|numberinput|numericinput)$"),
)


def _is_answer_form_category(value: object) -> bool:
    key = _section_key(value)
    if not key:
        return False
    return any(pattern.fullmatch(key) for pattern in _ANSWER_FORM_CATEGORY_PATTERNS)


def _question_category_is_section(question: QuestionRecord) -> bool:
    return bool(_section_key(question.category)) and not _is_answer_form_category(question.category)


def _wanted_section(
    question: QuestionRecord,
    section_overrides: dict[str, str],
) -> tuple[str, str]:
    """Return normalized section plus its audit basis.

    Explicit reviewed config has precedence over workbook category metadata. The
    latter is used only when it is not one of the known answer-form categories.
    """

    question_key = normalize_key(question.question)
    override = section_overrides.get(question_key, "")
    if override:
        return _section_key(override), "explicit-section"
    if _question_category_is_section(question):
        return _section_key(question.category), "qa-category-section"
    return "", ""


def _filter_candidates_by_section(
    candidates: list[int],
    fields: list[dict[str, Any]],
    wanted: str,
) -> list[int]:
    if not candidates or not wanted:
        return candidates
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
    sections: dict[str, str] | None = None,
) -> MatchAudit:
    """One-to-one deterministic matcher between customer QA and live Makro fields.

    No fuzzy similarity is used. Matching accepts only exact normalized
    label/attribute keys or explicit configured aliases. A reviewed section
    override can disambiguate duplicate generic labels; otherwise genuine
    ambiguity remains fail-closed.
    """

    alias_map = aliases or {}
    section_map = sections or {}
    fields = list(semantic_fields)
    keys_by_index = [_field_keys(field) for field in fields]
    used: set[int] = set()
    matches: list[QuestionFieldMatch] = []

    for question in catalog.questions:
        wanted_section, section_basis = _wanted_section(question, section_map)
        resolved: list[tuple[int, str]] = []
        for key, basis in _question_keys(question, alias_map):
            candidates = [
                index
                for index, field_keys in enumerate(keys_by_index)
                if index not in used and key in field_keys
            ]
            candidates = _filter_candidates_by_section(
                candidates,
                fields,
                wanted_section,
            )
            if candidates:
                effective_basis = (
                    f"{basis}+{section_basis}" if section_basis else basis
                )
                resolved.extend((index, effective_basis) for index in candidates)
                if basis == "exact-normalized":
                    break

        unique_indexes = sorted({index for index, _ in resolved})
        if not unique_indexes:
            section_detail = (
                f" 期望 live section={wanted_section!r}（{section_basis}）。"
                if wanted_section
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
