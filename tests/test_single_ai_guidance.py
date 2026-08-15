from __future__ import annotations

from pathlib import Path

from app.listing_content_policy import (
    LISTING_AI_GUIDANCE_ENV,
    MODEL_NAME_KEYWORDS_ENV,
    field_content_policy,
)


def test_model_name_keywords_are_soft_candidate_terms(monkeypatch):
    monkeypatch.setenv(
        MODEL_NAME_KEYWORDS_ENV,
        "inflatable mattress, air bed, camping mattress",
    )

    policy = field_content_policy({"label": "Model Name", "attribute_key": "model_name"})

    assert policy["model_name_candidate_keywords"] == (
        "inflatable mattress, air bed, camping mattress"
    )
    instruction = str(policy["instruction"])
    assert "Use only terms that are genuinely relevant" in instruction
    assert "omit irrelevant or unsupported terms" in instruction
    assert "never keyword-stuff" in instruction


def test_ai_guidance_is_context_not_evidence(monkeypatch):
    monkeypatch.setenv(
        LISTING_AI_GUIDANCE_ENV,
        "Emphasise portability and outdoor use without changing product facts",
    )

    policy = field_content_policy({"label": "Description", "attribute_key": "description"})

    assert policy["ai_guidance"] == (
        "Emphasise portability and outdoor use without changing product facts"
    )


def test_single_gui_installs_both_guidance_inputs_before_compact_layout():
    source = Path("gui/single_ai_guidance.py").read_text(encoding="utf-8")
    startup = Path("run_local_gui.py").read_text(encoding="utf-8")

    assert "window.ai_guidance_input = guidance" in source
    assert "window.model_name_keywords_input = keywords" in source
    assert "layout.insertLayout(3, row)" in source
    assert startup.index("install_single_ai_guidance(window)") < startup.index(
        "install_single_top_compact(window)"
    )
