"""Semantic resolution for Makro Step 1 live Vertical search.

Product Identity answers what the supplier is selling. This module turns that
identity into several retrieval intents, merges the query-owned Vertical rows
Makro actually returned, and lets AI choose only from that exact live pool.
Search terms are retrieval hints, never marketplace truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .listing_creation import JSONTaskProvider, ListingBootstrapHints, normalize_label


_MAX_SEARCH_TERMS = 5
_MAX_LIVE_CANDIDATES = 40
_QUERY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '&/()+.,-]*$")
_DISALLOWED_QUERY_WORDS = {"makro", "marketplace", "listing", "seller", "vertical", "category"}


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
    return not bool(words & _DISALLOWED_QUERY_WORDS)


def _identity(hints: ListingBootstrapHints) -> dict[str, Any]:
    return dict(hints.product_identity or {})


def _canonical_product_type(hints: ListingBootstrapHints) -> str:
    identity = _identity(hints)
    value = _clean(identity.get("product_type_en"))
    if value:
        return value
    return _clean(hints.vertical_search_terms[0] if hints.vertical_search_terms else "")


def _fallback_search_terms(hints: ListingBootstrapHints) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in (*hints.vertical_search_terms, _canonical_product_type(hints)):
        value = _clean(raw)
        key = _query_key(value)
        if not _usable_query(value) or not key or key in seen:
            continue
        seen.add(key)
        output.append(value)
        if len(output) >= _MAX_SEARCH_TERMS:
            break
    return tuple(output)


def build_vertical_search_plan_request(hints: ListingBootstrapHints) -> dict[str, Any]:
    product_type = _canonical_product_type(hints)
    identity = _identity(hints)
    return {
        "task": "plan_makro_vertical_search_intents",
        "system_instruction": (
            "Plan a small set of English marketplace search queries for finding the category of one "
            "known physical product. Queries are retrieval intents only: never claim or invent an "
            "actual Makro Vertical. JSON only."
        ),
        "prompt_instruction": (
            "Create complementary short noun-phrase searches for the physical product in "
            "context.product_identity. Focus on the stable product class and common product-type "
            "wording. Do not let incidental attributes dominate retrieval."
        ),
        "context": {
            "product_type_en": product_type,
            "product_summary": hints.product_summary,
            "product_identity": identity,
        },
        "rules": [
            "Return 3 to 5 concise English product-type noun phrases when possible.",
            "Keep the physical product itself central in every query.",
            "Prefer the core product class, a common synonym, and a useful head-noun variant.",
            "Drop model numbers, brand, colour, size, power source, rechargeable/battery wording and marketing adjectives unless they define a genuinely different product class.",
            "Do not output Makro Vertical names unless they independently arise as ordinary product wording; these strings are only search queries.",
            "Do not output marketplace, seller, listing, vertical, category, or platform terminology.",
            "Do not broaden into accessories, spare parts or adjacent products unless the supplied product itself is one.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "queries": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 5,
                    "items": {"type": "string", "minLength": 2},
                }
            },
            "required": ["queries"],
        },
        "strict_json_schema": True,
    }


def _normalize_search_terms(values: Iterable[object]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _clean(raw)
        key = _query_key(value)
        if not _usable_query(value) or not key or key in seen:
            continue
        seen.add(key)
        output.append(value)
        if len(output) >= _MAX_SEARCH_TERMS:
            break
    return tuple(output)


def plan_vertical_search_terms(
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
) -> tuple[str, ...]:
    """Return semantic retrieval intents; use Product Identity only as fallback.

    A successful planner replaces the raw Product Identity phrase for retrieval.
    This prevents incidental attributes in ``product_type_en`` (for example a
    power-source modifier) from being reintroduced after AI already produced
    cleaner product-class queries. The grounded identity seed is used only when
    planning fails or yields no safe query at all.
    """

    try:
        raw = provider.extract_json(build_vertical_search_plan_request(hints))
    except Exception:
        raw = None

    if isinstance(raw, dict):
        planned = _normalize_search_terms(raw.get("queries") or [])
        if planned:
            return planned

    fallback = _normalize_search_terms(_fallback_search_terms(hints))
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
    """Merge exact query-owned rows without interpreting their product meaning."""

    records: dict[str, dict[str, Any]] = {}
    sequence = 0
    for raw_query, raw_rows in observations:
        query = _clean(raw_query)
        query_key = _query_key(query)
        if not query_key:
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
                record = {
                    "label": label,
                    "queries": [],
                    "best_rank": rank,
                    "first_seen": sequence,
                }
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


def build_vertical_pool_choice_request(
    hints: ListingBootstrapHints,
    search_terms: tuple[str, ...],
    candidates: list[VerticalCandidateEvidence],
) -> dict[str, Any]:
    allowed = [item.label for item in candidates]
    return {
        "task": "choose_exact_makro_vertical_from_aggregated_live_search",
        "system_instruction": (
            "Choose exactly one Makro Vertical from live candidates returned by several marketplace "
            "search queries. The candidate set is authoritative; search-query wording is not. JSON only."
        ),
        "prompt_instruction": (
            "Select the candidate whose full breadcrumb/leaf actually represents the physical product "
            "in context.product_identity. Use all live candidates together instead of deciding from one "
            "query in isolation."
        ),
        "context": {
            "product_summary": hints.product_summary,
            "product_identity": _identity(hints),
            "search_queries": list(search_terms),
            "live_candidates": [item.as_dict() for item in candidates],
        },
        "rules": [
            "selected_vertical must be copied exactly from an allowed live candidate label or be empty.",
            "The search query that retrieved a row is only a retrieval hint, not evidence that the row is correct.",
            "Judge the full breadcrumb and especially its leaf against the physical product identity.",
            "Reject a candidate that merely shares a modifier, power term, model-like token or other incidental word with the product.",
            "Do not choose chargers, batteries, spare parts, accessories or adjacent products unless the product itself is one.",
            "Prefer the most specific candidate that describes the same physical product type.",
            "If none clearly describes the same product type, return an empty string so taxonomy fallback can run.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selected_vertical": {"type": "string", "enum": ["", *allowed]},
            },
            "required": ["selected_vertical"],
        },
        "strict_json_schema": True,
    }


def choose_vertical_candidate_pool(
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    search_terms: tuple[str, ...],
    candidates: list[VerticalCandidateEvidence],
) -> str:
    if not candidates:
        return ""
    raw = provider.extract_json(build_vertical_pool_choice_request(hints, search_terms, candidates))
    if not isinstance(raw, dict):
        raise ValueError("aggregated Vertical chooser response must be a JSON object")
    selected = _clean(raw.get("selected_vertical"))
    if not selected:
        return ""
    wanted = normalize_label(selected)
    matches = [item.label for item in candidates if normalize_label(item.label) == wanted]
    if len(matches) != 1:
        raise ValueError(
            f"AI returned a Vertical that is not one unique aggregated live candidate: {selected!r}"
        )
    return matches[0]


def matched_queries_for_candidate(
    candidates: list[VerticalCandidateEvidence],
    selected: str,
) -> tuple[str, ...]:
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
]
