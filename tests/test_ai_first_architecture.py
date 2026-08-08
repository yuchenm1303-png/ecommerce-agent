from __future__ import annotations

import inspect
from pathlib import Path

import makro_plan_listing
import makro_preview_listing
import makro_resolve_ai
from app import fill_plan, hard_field_validators, web_enrichment
from app.providers import openai_compatible, openai_semantic


LEGACY_PRODUCT_SEMANTIC_MODULES = (
    "app/answer_resolver.py",
    "app/resolution_engine.py",
    "app/semantic_extraction.py",
    "app/semantic_sources.py",
    "app/question_matcher.py",
    "app/alias_config.py",
    "app/value_normalization.py",
    "app/snapshot_evidence.py",
    "app/fact_validators.py",
)


def test_legacy_product_semantic_modules_are_not_in_repository():
    root = Path(__file__).resolve().parents[1]
    assert [path for path in LEGACY_PRODUCT_SEMANTIC_MODULES if (root / path).exists()] == []


def test_production_path_has_no_legacy_semantic_import_or_rule_layer():
    modules = (
        makro_resolve_ai,
        makro_plan_listing,
        makro_preview_listing,
        fill_plan,
        hard_field_validators,
        web_enrichment,
        openai_compatible,
        openai_semantic,
    )
    forbidden = (
        "answer_resolver",
        "resolution_engine",
        "semantic_sources",
        "semantic_extraction",
        "question_matcher",
        "alias_config",
        "verify_deterministic_synthesis",
        "ai_synthesis",
    )
    for module in modules:
        source = inspect.getsource(module)
        for token in forbidden:
            assert token not in source, f"{module.__name__} reintroduced {token}"


def test_ai_resolver_is_whole_product_local_plus_optional_single_web_pass():
    source = inspect.getsource(makro_resolve_ai)
    web_source = inspect.getsource(web_enrichment)
    assert "run_ai_resolution(" in source
    assert "run_web_enrichment(" in source
    assert "one_local_whole_product_call_plus_optional_one_sourced_web_call" in source
    assert "source_concurrency" not in source
    assert "batch_size" not in source
    assert "for decision in targets" not in web_source
    assert "provider.search_json(" in web_source


def test_hard_validator_contains_no_product_attribute_marker_tables():
    source = inspect.getsource(hard_field_validators)
    assert "G_SENSOR" not in source
    assert "DUAL_CAMERA" not in source
    assert "COLOUR_ALIASES" not in source
    assert "BRACKET_MARKERS" not in source
    assert "FOV" not in source
