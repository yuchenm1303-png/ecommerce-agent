from __future__ import annotations

import inspect
from pathlib import Path

import makro_execute_listing
import makro_plan_listing
import makro_preview_listing
import makro_resolve_ai
from app import ai_decisions, compact_evidence, fill_plan, hard_field_validators, image_evidence, product_facts, web_enrichment
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


def test_production_path_has_no_legacy_semantic_rule_layer():
    modules = (
        makro_resolve_ai,
        makro_plan_listing,
        makro_execute_listing,
        fill_plan,
        hard_field_validators,
        product_facts,
        image_evidence,
        compact_evidence,
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


def test_production_resolver_is_product_url_compact_facts_then_unresolved_web_only():
    source = inspect.getsource(makro_resolve_ai)
    web_source = inspect.getsource(web_enrichment)
    assert "capture_product_source(" in source
    assert "run_product_facts(" in source
    assert "run_image_evidence(" in source
    assert "run_web_enrichment(" in source
    assert "build_ai_product_context" not in source
    assert "ResolutionInputSpec" not in source
    assert "run_product_profile(" not in source
    assert "product-profile.json" not in source
    assert "compact-evidence.json" in source
    assert "image-observations.json" in source
    assert "product_url_capture_then_compact_product_facts_then_unresolved_web_fill" in source
    assert "final_resolve" not in source
    assert "final_provider" not in web_source
    assert "_run_final_resolution" not in web_source


def test_resolver_has_no_manual_product_identity_inputs():
    parser = makro_resolve_ai.build_parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert "--product-url" in options
    assert "--sku" not in options
    assert "--qa" not in options
    assert "--expected-model" not in options
    assert "--expected-brand" not in options
    assert "--product-table" not in options
    assert "--facts-json" not in options
    assert "--image-batch-size" in options
    assert "--image-concurrency" in options
    assert "--text-batch-chars" not in options
    assert "--text-concurrency" not in options


def test_image_evidence_batching_is_mechanical_and_has_no_product_field_rules():
    source = inspect.getsource(image_evidence).casefold()
    assert "batch_size" in source
    assert "image_sha256" in source
    for token in ("gps", "fov", "camera_position", "packaging_type", "vehicle_brand"):
        assert token not in source


def test_compact_evidence_contains_no_product_field_rules():
    source = inspect.getsource(compact_evidence).casefold()
    assert "image_fact_count" in source
    assert "text_source_count" in source
    for token in ("gps", "fov", "camera_position", "packaging_type", "vehicle_brand"):
        assert token not in source


def test_direct_executor_has_same_single_url_product_boundary():
    parser = makro_execute_listing.build_parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert "--product-url" in options
    assert "--decision-packet" in options
    assert "--live-schema" in options
    assert "--supplier-snapshot" in options
    assert "--image" in options
    assert "--sku" not in options
    assert "--qa" not in options
    source = inspect.getsource(makro_execute_listing)
    assert "build_ai_product_context" not in source
    assert "ResolutionInputSpec" not in source
    assert "generated_business_bundle" in source
    assert "send_to_qc_clicked" in source


def test_legacy_preview_cli_is_not_the_new_production_entrypoint():
    assert makro_preview_listing is not makro_execute_listing


def test_product_facts_resolves_all_fields_globally_without_repeating_images():
    source = inspect.getsource(product_facts)
    assert "resolve_compact_product_facts" in source
    assert "product_profile" not in source
    assert "image_path" not in source
    assert "ThreadPoolExecutor" not in source


def test_web_is_unresolved_only_and_receives_raw_primary_source_identity():
    source = inspect.getsource(web_enrichment)
    assert "WEB_FILLABLE_STATUSES = {MISSING, REVIEW}" in source
    assert "source_product_url" in source
    assert "product_fingerprint" in source
    assert "similar_product" in source
    assert "Actually invoke the built-in Web search tool now" in source
    assert "separate text-only inference stage" in source


def test_python_citation_guard_validates_source_address_not_literal_semantics():
    source = inspect.getsource(ai_decisions)
    assert "_citation_is_grounded" not in source
    assert "wanted in _normalize_ws(source.content)" not in source
    assert "grounding.by_id(reference) is not None" in source


def test_hard_validator_contains_no_product_attribute_marker_tables():
    source = inspect.getsource(hard_field_validators)
    assert "G_SENSOR" not in source
    assert "DUAL_CAMERA" not in source
    assert "COLOUR_ALIASES" not in source
    assert "BRACKET_MARKERS" not in source
    assert "FOV" not in source
