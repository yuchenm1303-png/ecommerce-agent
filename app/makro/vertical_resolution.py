"""Semantic resolution for Makro Step 1 live Vertical search.

Product Identity answers what the supplier is selling. This module converts that
identity into a bounded search ladder, merges query-owned live Makro rows, and lets
AI choose only from that exact live pool. Search terms are retrieval hints, never
marketplace truth.

Selection policy is intentionally practical for a sparse marketplace taxonomy:
prefer the same product type, then a genuine broader class, then the closest
reasonable live best-fit category when Makro does not expose an exact match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .listing_creation import JSONTaskProvider, ListingBootstrapHints, normalize_label
from .requested_vertical import (
    current_requested_vertical,
    requested_vertical_matches_label,
    requested_vertical_query,
)


_MAX_SEARCH_TERMS = 5
_MAX_LIVE_CANDIDATES = 120
_QUERY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '&/()+.,-]*$")
_FORBIDDEN_PLATFORM_WORDS = {"makro", "marketplace", "seller", "listing"}
_GENERIC_ONLY_QUERY_WORDS = {"vertical", "category", "product"}
_SAME_PRODUCT_TYPE = "same_product_type"
_BROADER_VALID_CLASS = "broader_valid_class"
_BEST_AVAILABLE_FIT = "best_available_fit"
_NO_VALID_CLASS = "none"
_VALID_SELECTION_RELATIONS = {
    _SAME_PRODUCT_TYPE,
    _BROADER_VALID_CLASS,
    _BEST_AVAILABLE_FIT,
    _NO_VALID_CLASS,
}
_TOKEN_STOPWORDS = {
    "a", "an", "and", "for", "in", "of", "on", "or", "the", "to", "with",
}
_GENERIC_CLASS_NOUNS = {
    "appliance", "apparatus", "device", "equipment", "item", "machine",
    "product", "system", "tool", "unit",
}


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _query_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).casefold()).strip()


def _usable_query(value: object) -> bool:
    text = _clean(value)
    if len(text) < 2 or len(text) > 72 or not text.isascii() or not _QUERY_RE.fullmatch(text):
        return False
    key = _query_key(text)
    if not key or not re.search(r"[a-z]", key):
        return False
    words = set(key.split())
    if words & _FORBIDDEN_PLATFORM_WORDS:
        return False
    return not bool(words and words <= _GENERIC_ONLY_QUERY_WORDS)


def _usable_head_query(value: object) -> bool:
    if not _usable_query(value):
        return False
    words = _query_key(value).split()
    if not 1 <= len(words) <= 2:
        return False
    return not (len(words) == 1 and words[0] in _GENERIC_CLASS_NOUNS)


def _identity(hints: ListingBootstrapHints) -> dict[str, Any]:
    return dict(hints.product_identity or {})


def _canonical_product_type(hints: ListingBootstrapHints) -> str:
    identity = _identity(hints)
    value = _clean(identity.get("product_type_en"))
    if value:
        return value
    return _clean(hints.vertical_search_terms[0] if hints.vertical_search_terms else "")


def _normalize_search_terms(
    values: Iterable[object],
    *,
    limit: int = _MAX_SEARCH_TERMS,
) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _clean(raw)
        key = _query_key(value)
        if not _usable_query(value) or not key or key in seen:
            continue
        seen.add(key)
        output.append(value)
        if len(output) >= max(1, int(limit)):
            break
    return tuple(output)


def _append_unique_query(output: list[str], seen: set[str], raw: object) -> None:
    value = _clean(raw)
    key = _query_key(value)
    if not _usable_query(value) or not key or key in seen:
        return
    seen.add(key)
    output.append(value)


def _fallback_search_ladder(hints: ListingBootstrapHints) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in hints.vertical_search_terms:
        _append_unique_query(output, seen, raw)
        if len(output) >= _MAX_SEARCH_TERMS:
            return tuple(output)

    product_type = _canonical_product_type(hints)
    _append_unique_query(output, seen, product_type)

    words = re.findall(r"[A-Za-z0-9]+", product_type)
    if len(words) >= 3:
        _append_unique_query(output, seen, " ".join(words[-2:]))
    if words:
        head = words[-1]
        if _usable_head_query(head):
            _append_unique_query(output, seen, head)
    return tuple(output[:_MAX_SEARCH_TERMS])


def build_vertical_search_plan_request(hints: ListingBootstrapHints) -> dict[str, Any]:
    product_type = _canonical_product_type(hints)
    return {
        "task": "plan_makro_vertical_search_intents",
        "system_instruction": (
            "Plan a bounded English search ladder for finding one physical product's marketplace "
            "category. Search strings are retrieval intents only; never claim or invent an actual "
            "Makro Vertical. JSON only."
        ),
        "prompt_instruction": (
            "Create a specific-to-broad retrieval ladder from context.product_identity. The goal is "
            "high recall without letting incidental attributes dominate search."
        ),
        "context": {
            "product_type_en": product_type,
            "product_summary": hints.product_summary,
            "product_identity": _identity(hints),
        },
        "rules": [
            "specific_queries: return 1 or 2 concise product-type phrases that closely name the physical product.",
            "broader_queries: return 0 to 2 progressively broader product-family phrases by removing qualifiers, not by switching to unrelated products.",
            "head_noun_query: return the shortest useful common class noun or two-word head phrase that a human would type for broad marketplace recall.",
            "The final ladder must behave like specific -> broader -> head noun.",
            "Drop model numbers, brand, colour, size, power source, rechargeable/battery wording and marketing adjectives unless they define a genuinely different product class.",
            "Do not use Makro, marketplace, seller, listing, vertical or category as retrieval metadata.",
            "Do not deliberately broaden into accessories or spare parts unless the supplied product itself is one.",
            "The head noun may be broad because downstream selection is constrained to real live Makro rows and evaluates the complete breadcrumb.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "specific_queries": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string", "minLength": 2}},
                "broader_queries": {"type": "array", "minItems": 0, "maxItems": 2, "items": {"type": "string", "minLength": 2}},
                "head_noun_query": {"type": "string", "minLength": 2},
            },
            "required": ["specific_queries", "broader_queries", "head_noun_query"],
        },
        "strict_json_schema": True,
    }


def _planned_search_ladder(raw: dict[str, Any]) -> tuple[str, ...]:
    specific = _normalize_search_terms(raw.get("specific_queries") or (), limit=2)
    broader = _normalize_search_terms(raw.get("broader_queries") or (), limit=2)
    head = _clean(raw.get("head_noun_query"))
    if not specific or not _usable_head_query(head):
        return ()
    output: list[str] = []
    seen: set[str] = set()
    for term in (*specific, *broader):
        if len(output) >= _MAX_SEARCH_TERMS - 1:
            break
        _append_unique_query(output, seen, term)
    head_key = _query_key(head)
    if head_key in seen:
        output = [term for term in output if _query_key(term) != head_key]
        seen = {_query_key(term) for term in output}
    _append_unique_query(output, seen, head)
    return tuple(output[:_MAX_SEARCH_TERMS])


def plan_vertical_search_terms(provider: JSONTaskProvider, hints: ListingBootstrapHints) -> tuple[str, ...]:
    requested = current_requested_vertical()
    if requested:
        query = requested_vertical_query(requested)
        if not _usable_query(query):
            raise ValueError(
                f"手动指定类目无法转换成可用的 Makro Vertical 搜索词：{requested!r}"
            )
        return (query,)

    try:
        raw = provider.extract_json(build_vertical_search_plan_request(hints))
    except Exception:
        raw = None
    if isinstance(raw, dict):
        planned = _planned_search_ladder(raw)
        if planned:
            return planned
    fallback = _fallback_search_ladder(hints)
    if fallback:
        return fallback
    raise ValueError("Product Identity produced no safe Makro Vertical retrieval intent")


@dataclass(frozen=True, slots=True)
class VerticalCandidateEvidence:
    label: str
    matched_queries: tuple[str, ...]
    hit_count: int
    best_rank: int
    first_seen: int

    @property
    def leaf_label(self) -> str:
        parts = [part.strip() for part in self.label.split("/") if part.strip()]
        return parts[-1] if parts else self.label

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "leaf_label": self.leaf_label,
            "matched_queries": list(self.matched_queries),
            "query_hit_count": self.hit_count,
            "best_query_rank": self.best_rank,
        }


def merge_vertical_search_observations(
    observations: Iterable[tuple[str, list[str]]],
    *,
    max_candidates: int = _MAX_LIVE_CANDIDATES,
) -> list[VerticalCandidateEvidence]:
    records: dict[str, dict[str, Any]] = {}
    sequence = 0
    for raw_query, raw_rows in observations:
        query = _clean(raw_query)
        if not _query_key(query):
            continue
        seen_this_query: set[str] = set()
        for rank, raw_label in enumerate(raw_rows, start=1):
            label = _clean(raw_label)
            key = normalize_label(label)
            if not label or not key or key in seen_this_query:
                continue
            seen_this_query.add(key)
            record = records.get(key)
            if record is None:
                sequence += 1
                record = {"label": label, "queries": [], "best_rank": rank, "first_seen": sequence}
                records[key] = record
            if query not in record["queries"]:
                record["queries"].append(query)
            record["best_rank"] = min(int(record["best_rank"]), rank)
    merged = [
        VerticalCandidateEvidence(
            label=str(record["label"]),
            matched_queries=tuple(record["queries"]),
            hit_count=len(record["queries"]),
            best_rank=int(record["best_rank"]),
            first_seen=int(record["first_seen"]),
        )
        for record in records.values()
    ]
    merged.sort(key=lambda item: (-item.hit_count, item.best_rank, item.first_seen))
    return merged[: max(1, int(max_candidates))]


def _exact_product_type_candidate(
    hints: ListingBootstrapHints,
    candidates: list[VerticalCandidateEvidence],
) -> str:
    """Return one uniquely exact live leaf for the canonical physical product type.

    Exact Makro truth should not be demoted by a probabilistic best-fit decision.
    This is deliberately narrow: only the live leaf itself may exactly equal the
    canonical Product Identity type after punctuation/case normalization. Broader,
    sibling and synonym decisions still go through the semantic chooser.
    """

    product_key = _query_key(_canonical_product_type(hints))
    if not product_key:
        return ""
    matches = [item.label for item in candidates if _query_key(item.leaf_label) == product_key]
    return matches[0] if len(matches) == 1 else ""


def _stem_category_token(token: str) -> str:
    value = str(token or "").casefold().strip()
    if len(value) > 4 and value.endswith("ies"):
        value = value[:-3] + "y"
    elif len(value) > 4 and value.endswith("sses"):
        value = value[:-2]
    elif len(value) > 4 and value.endswith(("ches", "shes", "xes", "zes")):
        value = value[:-2]
    elif len(value) > 3 and value.endswith("s") and not value.endswith("ss"):
        value = value[:-1]
    if len(value) > 5 and value.endswith("ing"):
        value = value[:-3]
    elif len(value) > 4 and value.endswith("ed"):
        value = value[:-2]
    if len(value) > 4 and value.endswith("er"):
        value = value[:-2]
    return value


def _meaningful_category_tokens(value: object) -> set[str]:
    output: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", _clean(value).casefold()):
        if raw in _TOKEN_STOPWORDS:
            continue
        token = _stem_category_token(raw)
        if not token or token in _GENERIC_CLASS_NOUNS or len(token) < 2:
            continue
        output.add(token)
    return output


def _product_semantic_tokens(hints: ListingBootstrapHints) -> set[str]:
    identity = _identity(hints)
    evidence = [_canonical_product_type(hints), hints.product_summary, identity.get("product_type_en", ""), identity.get("product_summary", "")]
    output: set[str] = set()
    for value in evidence:
        output.update(_meaningful_category_tokens(value))
    return output


def _token_is_supported(token: str, evidence_tokens: set[str]) -> bool:
    if token in evidence_tokens:
        return True
    if len(token) < 4:
        return False
    return any(len(existing) >= 4 and (token in existing or existing in token) for existing in evidence_tokens)


def unsupported_candidate_constraints(hints: ListingBootstrapHints, candidate_label: str) -> tuple[str, ...]:
    """Describe unsupported leaf qualifiers for diagnostics, not as a hard gate."""
    parts = [part.strip() for part in str(candidate_label or "").split("/") if part.strip()]
    leaf = parts[-1] if parts else str(candidate_label or "").strip()
    candidate_tokens = _meaningful_category_tokens(leaf)
    evidence_tokens = _product_semantic_tokens(hints)
    return tuple(sorted(token for token in candidate_tokens if not _token_is_supported(token, evidence_tokens)))


def build_vertical_pool_choice_request(
    hints: ListingBootstrapHints,
    search_terms: tuple[str, ...],
    candidates: list[VerticalCandidateEvidence],
) -> dict[str, Any]:
    allowed = [item.label for item in candidates]
    return {
        "task": "choose_exact_makro_vertical_from_aggregated_live_search",
        "system_instruction": (
            "Choose exactly one Makro Vertical from the supplied live candidates. The live candidate set is authoritative; "
            "never invent a Vertical. Makro taxonomy can be sparse, so choose the closest practical category when an exact class is unavailable. JSON only."
        ),
        "prompt_instruction": (
            "Compare the grounded physical product identity with every live breadcrumb. Rank choices as same product type first, "
            "genuine broader class second, then closest reasonable live best-fit category when Makro does not expose an exact class."
        ),
        "context": {
            "product_summary": hints.product_summary,
            "product_identity": _identity(hints),
            "search_queries_specific_to_broad": list(search_terms),
            "live_candidates": [item.as_dict() for item in candidates],
        },
        "rules": [
            "selected_vertical must be copied exactly from an allowed live candidate label or be empty.",
            "Search queries are retrieval hints only; broader/head queries intentionally trade precision for recall.",
            "Judge the complete breadcrumb, retail context and leaf against the physical product identity.",
            "Use same_product_type when the candidate represents the same physical product class.",
            "Use broader_valid_class when the candidate is a genuine semantic superclass that contains the product; a strict broader class must never add a different defining capability, mechanism, form, audience or use-case.",
            "If Makro exposes neither of those, use best_available_fit for the live category that a marketplace operator would most reasonably use to list this product despite taxonomy mismatch.",
            "For best_available_fit, prefer shared defining function, merchandise context and buyer expectation over literal word overlap.",
            "A nearby sibling or form-specific class may be used as best_available_fit when the portal offers no better category; do not mislabel it as a broader superclass.",
            "Avoid accessory, spare-part or consumable classes when a non-accessory live option is materially closer to the sold product.",
            "Return none only when every live candidate is plainly unrelated and selecting any of them would severely misrepresent what is being sold.",
            "Priority is same_product_type -> broader_valid_class -> best_available_fit -> none.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selected_vertical": {"type": "string", "enum": ["", *allowed]},
                "selection_relation": {"type": "string", "enum": [_SAME_PRODUCT_TYPE, _BROADER_VALID_CLASS, _BEST_AVAILABLE_FIT, _NO_VALID_CLASS]},
            },
            "required": ["selected_vertical", "selection_relation"],
        },
        "strict_json_schema": True,
    }


def choose_vertical_candidate_pool(
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    search_terms: tuple[str, ...],
    candidates: list[VerticalCandidateEvidence],
) -> str:
    requested = current_requested_vertical()
    if requested:
        if not candidates:
            raise ValueError(
                f"手动指定类目 {requested!r} 没有在 Makro 当前 live Vertical 搜索中返回任何候选。"
            )
        matches = [
            item.label
            for item in candidates
            if requested_vertical_matches_label(requested, item.label)
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            available = " | ".join(item.label for item in candidates[:20])
            raise ValueError(
                f"手动指定类目 {requested!r} 未匹配到唯一 Makro live Vertical；"
                f"当前候选={available or '<none>'}"
            )
        raise ValueError(
            f"手动指定类目 {requested!r} 同时匹配到多个 Makro live Vertical：{matches!r}"
        )

    if not candidates:
        return ""
    exact_product_type = _exact_product_type_candidate(hints, candidates)
    if exact_product_type:
        return exact_product_type
    raw = provider.extract_json(build_vertical_pool_choice_request(hints, search_terms, candidates))
    if not isinstance(raw, dict):
        raise ValueError("aggregated Vertical chooser response must be a JSON object")
    selected = _clean(raw.get("selected_vertical"))
    relation = _clean(raw.get("selection_relation")).casefold()
    if not relation:
        relation = _SAME_PRODUCT_TYPE if selected else _NO_VALID_CLASS
    if relation not in _VALID_SELECTION_RELATIONS:
        raise ValueError(f"invalid Makro Vertical selection_relation={relation!r}")
    if not selected:
        if relation != _NO_VALID_CLASS:
            raise ValueError("empty Makro Vertical selection requires selection_relation='none'")
        return ""
    if relation not in {_SAME_PRODUCT_TYPE, _BROADER_VALID_CLASS, _BEST_AVAILABLE_FIT}:
        raise ValueError("non-empty Makro Vertical selection requires same_product_type, broader_valid_class, or best_available_fit")
    wanted = normalize_label(selected)
    matches = [item.label for item in candidates if normalize_label(item.label) == wanted]
    if len(matches) != 1:
        raise ValueError(f"AI returned a Vertical that is not one unique aggregated live candidate: {selected!r}")
    return matches[0]


def matched_queries_for_candidate(candidates: list[VerticalCandidateEvidence], selected: str) -> tuple[str, ...]:
    wanted = normalize_label(selected)
    matches = [item for item in candidates if normalize_label(item.label) == wanted]
    if len(matches) != 1:
        return ()
    return matches[0].matched_queries


__all__ = [
    "VerticalCandidateEvidence",
    "build_vertical_pool_choice_request",
    "build_vertical_search_plan_request",
    "choose_vertical_candidate_pool",
    "matched_queries_for_candidate",
    "merge_vertical_search_observations",
    "plan_vertical_search_terms",
    "unsupported_candidate_constraints",
]
