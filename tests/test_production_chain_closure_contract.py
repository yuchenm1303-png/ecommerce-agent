from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (ROOT / "gui" / "required_input_support.py").read_text(encoding="utf-8")
WORKFLOW = (ROOT / "makro_gui_workflow.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "gui" / "readonly_runner.py").read_text(encoding="utf-8")
BATCH = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")
INFERENCE = (ROOT / "app" / "best_effort_inference.py").read_text(encoding="utf-8")


def test_formal_gui_never_synthesizes_unresolved_required_values() -> None:
    assert "required_fallback_override" not in REQUIRED
    assert '"source_type": "user"' in REQUIRED
    assert "必填 · 请填写真实值" in REQUIRED
    assert "Full Step 3 不会编造 required 值" in REQUIRED


def test_same_product_resume_is_bound_to_exact_prior_failed_page() -> None:
    assert '"--resume-current-url"' in WORKFLOW
    assert 'manifest["failed_phase"] = current' in WORKFLOW
    assert 'manifest["failed_page_url"] = failed_page_url' in WORKFLOW
    assert "len(listing_pages) != 1" in WORKFLOW
    assert "url == wanted" in WORKFLOW
    assert 'payload.get("failed_phase")' in RUNNER
    assert 'self.config.product_url.strip() != config.product_url.strip()' in RUNNER


def test_batch_ready_means_full_acceptance_prerequisites_are_present() -> None:
    assert "if job.required_blocked > 0:" in BATCH
    assert "elif job.image_count <= 0:" in BATCH
    assert "if not upload_images:" in BATCH
    assert "必须显式授权 Product Photos" in BATCH


def test_production_resolver_does_not_promote_category_guesses_to_ready() -> None:
    assert '"batched-product-facts" in extractor' in INFERENCE
    assert "production evidence-only policy: unresolved fields remain MISSING after Web" in INFERENCE
    assert "if _production_evidence_only(packet):" in INFERENCE
