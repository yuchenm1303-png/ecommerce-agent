"""Deterministic-first, AI-fallback placeholder interfaces.

Stagehand-style principle adopted without adding Stagehand as a dependency:
stable DOM structure and exact option matching stay deterministic; an AI
fallback may later be consulted only when deterministic semantic resolution has
no safe answer. No LLM is called in this task, and AI is never allowed to
invent SKU, price, MOQ, stock, shipping or other business facts.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.answer_resolver import ResolvedAnswer
from app.source_bundle import ProductSourceBundle


class SemanticFallback(Protocol):
    """Optional last-resort resolver consulted only on deterministic MISSING.

    Implementations must return ``None`` when they cannot produce an
    evidence-grounded answer; they must never guess product facts.
    """

    name: str

    def try_resolve(
        self, semantic_field: dict[str, Any], bundle: ProductSourceBundle
    ) -> ResolvedAnswer | None:
        """Return an evidence-grounded answer or None."""
        ...


class DeterministicOnlyFallback:
    """Default no-op fallback used until a real AI fallback is introduced.

    Keeps the future call site exercised in tests while guaranteeing that no
    external model is ever consulted by the current pipeline.
    """

    name = "deterministic-only"

    def try_resolve(
        self, semantic_field: dict[str, Any], bundle: ProductSourceBundle
    ) -> None:
        return None
