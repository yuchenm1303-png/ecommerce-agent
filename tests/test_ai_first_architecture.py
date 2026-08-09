from __future__ import annotations

import inspect
from pathlib import Path

import makro_plan_listing
import makro_preview_listing
import makro_resolve_ai
from app import ai_decisions, field_mapping, fill_plan, hard_field_validators, product_profile, web_enrichment
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
        product_profile,
        field_mapping,
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


def test_production_resolver_is_profile_local_fill_then_web_fill_only():
    source = inspect.getsource(makro_resolve_ai)
    web_source = inspect.getsource(web_enrichment)
    assert "run_product_profile(" in source
    assert "run_field_mapping(" in source
    assert "run_web_enrichment(" in source
    assert "run_ai_resolution(" not in source
    assert "product_profile_then_parallel_local_fill_then_parallel_web_fill" in source
    assert "final_resolve" not in source
    assert "final_provider" not in web_source
    assert "_run_final_resolution" not in web_source
    assert "_run_final_batch" not in web_source
    assert "--field-batch-size" in source
    assert "--field-concurrency" in source
    assert "--web-batch-size" in source
    assert "--web-concurrency" in source


def test_old_whole_product_field_resolution_runner_is_removed():
    source = inspect.getsource(ai_decisions)
    assert "run_ai_resolution" not in source
    assert "build_ai_resolution_request" not in source
    assert "AI_RESOLUTION_RULES" not in source


def test_images_are_used_only_by_product_understanding_not_field_mapping_or_web():
    profile_source = inspect.getsource(product_profile)
    mapping_source = inspect.getsource(field_mapping)
    web_source = inspect.getsource(web_enrichment)
    assert "grounding.as_request_list()" in profile_source
    assert '"target_fields": []' in profile_source
    assert "image_path" not in mapping_source
    assert "IMAGE_KIND" not in mapping_source
    assert "image_path" not in web_source
    assert "IMAGE_KIND" not in web_source


def test_field_mapping_batches_are_mechanical_not_category_semantic_tables():
    source = inspect.getsource(field_mapping)
    assert "_mechanical_batches" in source
    forbidden = (
        "camera_fields",
        "storage_fields",
        "dimension_fields",
        "vehicle_fields",
        "colour_aliases",
        "g_sensor",
    )
    lowered = source.casefold()
    for token in forbidden:
        assert token not in lowered


def test_hard_validator_contains_no_product_attribute_marker_tables():
    source = inspect.getsource(hard_field_validators)
    assert "G_SENSOR" not in source
    assert "DUAL_CAMERA" not in source
    assert "COLOUR_ALIASES" not in source
    assert "BRACKET_MARKERS" not in source
    assert "FOV" not in source
