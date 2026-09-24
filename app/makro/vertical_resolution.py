"""Semantic resolution for Makro Step 1 live Vertical search.

Product Identity is an initial interpretation of what the supplier is selling, not
an irreversible truth source. Step 1 keeps the supplier evidence and customer intent
separate, lets AI plan a bounded retrieval ladder, and lets AI choose only from the
Makro rows that are live in the current search generation.

The live Makro candidate set is authoritative: AI may correct an over-specific or
mistaken initial identity, but it can never invent a marketplace Vertical.
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


_MAX_SEARCH_TERMS = 7
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


def _reconciliation_context(hints: ListingBootstrapHints) -> dict[str, Any]:
    """Expose independent evidence channels without collapsing them into one AI fact."""

    return {
        "initial_product_identity": _identity(hints),
        "grounded_supplier_evidence": list(hints.grounded_product_evidence),
        "customer_listing_intent": _clean(hints.customer_intent),
    }


def _canonical_product_type(hints: ListingBootstrapHints) -> str:
    identity = _identity(hints)
    value = _clean(identity.get("product_type_en"))
    if value:
        return value
    return _clean(hints.vertical_search_terms[0] if hints.vertical_search_terms else "")


def _product_type_query_words(hints: ListingBootstrapHints) -> list[str]:
    return [
        word
        for word in _query_key(_canonical_product_type(hints)).split()
        if word and word not in _TOKEN_STOPWORDS
    ]


def _usable_head_query_for_product(hints: ListingBootstrapHints, value: object) -> bool:
    """Reject lossy one-word heads for an already multi-word product identity."""

    if not _usable_head_query(value):
        return False
    head_words = _query_key(value).split()
    product_words = _product_type_query_words(hints)
    if len(product_words) >= 2 and len(head_words) < 2:
        return False
    return True


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
        if _usable_head_query_for_product(hints, head):
            _append_unique_query(output, seen, head)
    return tuple(output[:_MAX_SEARCH_TERMS])


def build_vertical_search_plan_request(hints: ListingBootstrapHints) -> dict[str, Any]:
    product_type = _canonical_product_type(hints)
    evidence = _reconciliation_context(hints)
    return {
        "task": "plan_makro_vertical_search_intents",
        "system_instruction": (
            "Plan a bounded English marketplace-category retrieval ladder for one physical product. "
            "The initial Product Identity is an AI interpretation, not an irreversible truth source. "
            "Grounded supplier snippets and customer intent are independent evidence channels. Search "
            "strings are retrieval hypotheses only; never claim or invent an actual Makro Vertical. JSON only."
        ),
        "prompt_instruction": (
            "Create a specific-to-broad retrieval ladder by reconciling the initial identity with the "
            "grounded supplier snippets and customer intent. Preserve the core sold product class while "
            "preventing incidental material, personalization, colour or marketing attributes from trapping "
            "all queries in one mistaken interpretation. Include conventional retail vocabulary variants."
        ),
        "context": {
            "product_type_en": product_type,
            "product_summary": hints.product_summary,
            **evidence,
        },
        "rules": [
            "Treat initial_product_identity as a hypothesis supported by evidence, not as the sole authority for every later decision.",
            "Grounded supplier evidence is factual product evidence. Customer listing intent is useful independent context but must not override plainly contradictory supplier facts.",
            "specific_queries: return 1 or 2 concise phrases for the core sold physical product class; omit incidental material/personalization/style modifiers unless they define a genuinely different class.",
            "alternate_queries: return 0 to 2 conventional retail synonyms or function/form paraphrases that could recover the same item when marketplace vocabulary differs.",
            "When customer intent or grounded evidence supports a materially different but plausible class wording from the initial identity, reserve an alternate query for that supported wording instead of repeating the initial hypothesis.",
            "broader_queries: return 0 to 2 progressively broader product-family phrases by removing qualifiers, not by switching to unrelated products.",
            "head_noun_query: return the shortest useful common class phrase for broad marketplace recall.",
            "The final ladder must behave like core product class -> alternate retail vocabulary -> broader family -> discriminative head phrase.",
            "If the product type has multiple meaningful words, do not collapse head_noun_query to one bare functional/form noun; keep at least one differentiating modifier.",
            "Drop model numbers, brand, colour, size, material, engraving/personalization, power source and marketing adjectives unless they define a genuinely different product class.",
            "Do not use Makro, marketplace, seller, listing, vertical or category as retrieval metadata.",
            "Do not deliberately broaden into accessories or spare parts unless the supplied product itself is one.",
            "Alternate queries must remain plausible names for the same sold physical item, not an accessory, consumable or neighboring product.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "specific_queries": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string", "minLength": 2}},
                "alternate_queries": {"type": "array", "minItems": 0, "maxItems": 2, "items": {"type": "string", "minLength": 2}},
                "broader_queries": {"type": "array", "minItems": 0, "maxItems": 2, "items": {"type": "string", "minLength": 2}},
                "head_noun_query": {"type": "string", "minLength": 2},
            },
            "required": ["specific_queries", "alternate_queries", "broader_queries", "head_noun_query"],
        },
        "strict_json_schema": True,
    }


def _planned_search_ladder(raw: dict[str, Any], hints: ListingBootstrapHints) -> tuple[str, ...]:
    specific = _normalize_search_terms(raw.get("specific_queries") or (), limit=2)
    alternate = _normalize_search_terms(raw.get("alternate_queries") or (), limit=2)
    broader = _normalize_search_terms(raw.get("broader_queries") or (), limit=2)
    head = _clean(raw.get("head_noun_query"))
    if not specific:
        return ()
    output: list[str] = []
    seen: set[str] = set()
    for term in (*specific, *alternate, *broader):
        if len(output) >= _MAX_SEARCH_TERMS - 1:
            break
        _append_unique_query(output, seen, term)
    if _usable_head_query_for_product(hints, head):
        head_key = _query_key(head)
        if head_key in seen:
            output = [term for term in output if _query_key(term) != head_key]
            seen = {_query_key(term) for term in output}
        _append_unique_query(output, seen, head)
    return tuple(output[:_MAX_SEARCH_TERMS])


def _with_canonical_product_type_fallback(
    hints: ListingBootstrapHints,
    terms: tuple[str, ...],
) -> tuple[str, ...]:
    product_type = _canonical_product_type(hints)
    product_key = _query_key(product_type)
    output = list(terms)
    seen = {_query_key(term) for term in output if _query_key(term)}
    if product_key and product_key not in seen and _usable_query(product_type):
        if len(output) >= _MAX_SEARCH_TERMS:
            output = output[: _MAX_SEARCH_TERMS - 1]
        output.append(product_type)
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
        planned = _planned_search_ladder(raw, hints)
        if planned:
            return _with_canonical_product_type_fallback(hints, planned)
    fallback = _fallback_search_ladder(hints)
    if fallback:
        return _with_canonical_product_type_fallback(hints, fallback)
    raise ValueError("Product evidence produced no safe Makro Vertical retrieval intent")


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
    evidence: list[object] = [
        _canonical_product_type(hints),
        hints.product_summary,
        identity.get("product_type_en", ""),
        identity.get("product_summary", ""),
        hints.customer_intent,
        *hints.grounded_product_evidence,
    ]
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
    evidence = _reconciliation_context(hints)
    return {
        "task": "choose_exact_makro_vertical_from_aggregated_live_search",
        "system_instruction": (
            "Choose exactly one Makro Vertical from the supplied current live candidates, or return none. "
            "The current live candidate set is authoritative; never invent a Vertical. Reconcile the initial AI identity "
            "against its cited raw supplier evidence and customer intent before deciding. JSON only."
        ),
        "prompt_instruction": (
            "Decide only from the rows that are live in this search generation. If none of them is a valid fit, return none "
            "so the caller can continue the planned retrieval ladder. Do not select an unrelated row merely to avoid none."
        ),
        "context": {
            "product_summary": hints.product_summary,
            **evidence,
            "search_queries_specific_to_broad": list(search_terms),
            "live_candidates": [item.as_dict() for item in candidates],
        },
        "rules": [
            "selected_vertical must be copied exactly from an allowed live candidate label or be empty.",
            "Search queries are retrieval hints only; query wording and result rank are not semantic proof that a row is correct.",
            "Initial product identity is an interpretation, not an immutable truth source. Grounded supplier snippets are independent factual evidence.",
            "Customer listing intent is independent context: use it to detect and correct a plausible identity misunderstanding, but never let it override supplier evidence that clearly describes a different physical item.",
            "Distinguish core product class from incidental attributes such as material, engraving, personalization, colour, size or marketing adjectives.",
            "A live candidate that matches the core function/form supported by raw evidence may be better than a candidate that only matches incidental words from the initial identity.",
            "Use same_product_type when the candidate represents the same physical product class.",
            "Use broader_valid_class when the candidate is a genuine semantic superclass that contains the product; it must not add a different defining capability, mechanism, form, audience or use-case.",
            "Use best_available_fit only when one current live row is genuinely the marketplace's closest practical class for the sold product.",
            "Avoid accessory, spare-part or consumable classes when the sold product is not one.",
            "A candidate returned only because of generic word overlap is not valid unless the complete breadcrumb independently matches the sold product.",
            "Return none whenever the current live rows are unrelated or too weak to justify a category choice; later retrieval queries may provide better rows.",
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
    raw = provider.extract_json(build_vertical_pool_choice_request(hints, search_terms, candidates))
    if not isinstance(raw, dict):
        raise ValueError("live Vertical chooser response must be a JSON object")
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
        raise ValueError(f"AI returned a Vertical that is not one unique current live candidate: {selected!r}")
    return matches[0]


__all__ = [
    "VerticalCandidateEvidence",
    "build_vertical_pool_choice_request",
    "build_vertical_search_plan_request",
    "choose_vertical_candidate_pool",
    "merge_vertical_search_observations",
    "plan_vertical_search_terms",
    "unsupported_candidate_constraints",
]
