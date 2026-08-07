"""Deterministic-first, AI-fallback placeholder tests.

No LLM is ever called: the default pipeline uses no fallback, and the
DeterministicOnlyFallback placeholder returns None without side effects.
"""

from __future__ import annotations

from app.answer_resolver import MISSING, NEEDS_REVIEW, RESOLVED, ResolvedAnswer, resolve_field
from app.makro.fallback import DeterministicOnlyFallback
from app.source_bundle import ProductSourceBundle


def _field(key, label, options=None, multi_value=False):
    return {
        "attribute_key": key,
        "label": label,
        "multi_value": multi_value,
        "controls": [{"name": "{0}_0_value".format(key), "field_kind": "input", "options": options or []}],
    }


def test_deterministic_only_fallback_is_a_safe_noop():
    fallback = DeterministicOnlyFallback()
    assert fallback.name == "deterministic-only"

    answer = resolve_field(_field("waterproof_depth", "Waterproof Depth"), ProductSourceBundle(), fallback=fallback)

    assert answer.status == MISSING
    assert answer.answer is None


def test_fallback_consulted_only_when_deterministic_missing():
    class GuessingFallback:
        name = "test-fallback"

        def try_resolve(self, semantic_field, bundle):
            return ResolvedAnswer(
                attribute_key="waterproof_depth",
                label="Waterproof Depth",
                status=RESOLVED,
                answer="10m",
                answer_values=["10m"],
                detail="fallback placeholder",
            )

    answer = resolve_field(
        _field("waterproof_depth", "Waterproof Depth"),
        ProductSourceBundle(),
        fallback=GuessingFallback(),
    )

    assert answer.status == RESOLVED
    assert answer.answer_values == ["10m"]


def test_fallback_not_consulted_when_deterministic_resolves():
    calls = []

    class CountingFallback:
        name = "count"

        def try_resolve(self, semantic_field, bundle):
            calls.append(semantic_field)
            return None

    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Model Number", value="L11", source_type="structured",
        source_reference="table.xlsx:row=2", priority=10,
    )

    answer = resolve_field(_field("model_number", "Model Number"), bundle, fallback=CountingFallback())

    assert answer.status == RESOLVED
    assert calls == []


def test_business_fields_never_consult_fallback():
    calls = []

    class ExplodingFallback:
        name = "explode"

        def try_resolve(self, semantic_field, bundle):
            calls.append(semantic_field)
            raise AssertionError("business field must never reach a fallback")

    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Base Price", value="999", source_type="customer_file",
        source_reference="qa.xlsx:row=8", priority=20,
    )

    answer = resolve_field(_field("mrp", "Base Price"), bundle, fallback=ExplodingFallback())

    assert answer.status == NEEDS_REVIEW
    assert calls == []
    assert "经营字段" in answer.detail
