from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
WINDOW = (ROOT / "gui" / "product_input_window.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "gui" / "readonly_runner.py").read_text(encoding="utf-8")
PACK_WORKFLOW = (ROOT / "makro_product_pack_workflow.py").read_text(encoding="utf-8")
RESOLVER = (ROOT / "makro_resolve_ai.py").read_text(encoding="utf-8")
SHARED_WORKFLOW = (ROOT / "makro_gui_workflow.py").read_text(encoding="utf-8")
REAL_GUI = (ROOT / "gui" / "real_execution.py").read_text(encoding="utf-8")
EXECUTOR = (ROOT / "makro_execute_listing.py").read_text(encoding="utf-8")


def test_formal_gui_uses_product_input_window() -> None:
    assert "from gui.product_input_window import ProductInputWorkflowMainWindow" in RUN
    assert "MainWindow = ProductInputWorkflowMainWindow" in RUN
    assert "上传资料…" in WINDOW
    for suffix in ("*.pdf", "*.docx", "*.xlsx", "*.csv", "*.txt", "*.png", "*.zip"):
        assert suffix in WINDOW


def test_runner_routes_product_files_to_pack_workflow() -> None:
    assert "product_files: tuple[str, ...] = ()" in RUNNER
    assert 'script = "makro_product_pack_workflow.py" if is_pack else "makro_gui_workflow.py"' in RUNNER
    assert 'args.extend(["--product-file", path])' in RUNNER
    assert "供应商 URL 或客户资料包" in RUNNER


def test_pack_workflow_reuses_canonical_listing_state_machine() -> None:
    for marker in (
        "_advance_listing_to_step3",
        "_create_fresh_owned_page",
        "prepare_owned_step1_page",
        "_scan_and_write_live_schema",
        "_resolver_command",
        "_plan_command",
    ):
        assert marker in PACK_WORKFLOW
    assert "send_to_qc_clicked" in PACK_WORKFLOW
    assert "send_to_qc=False" in PACK_WORKFLOW


def test_resolver_accepts_product_files_and_reusable_pack_manifest() -> None:
    assert '"--product-file"' in RESOLVER
    assert '"--product-pack-manifest"' in RESOLVER
    assert "run_resolver" in RESOLVER


def test_text_only_customer_evidence_is_valid_strict_rebind() -> None:
    plan = SHARED_WORKFLOW.split("def _plan_command(", 1)[1].split("def _run_resolver_pair", 1)[0]
    assert "if not decision_packet or not snapshot:" in plan
    assert "not evidence_images" not in plan

    prepare = REAL_GUI.split("def _prepare_inputs", 1)[1].split("def _read_output", 1)[0]
    assert 'missing.append("evidence_images=<missing>")' not in prepare

    validate = EXECUTOR.split("def _validate_args", 1)[1].split("def _scan_semantic_fields", 1)[0]
    assert "if not args.image:" not in validate
    assert "if not args.supplier_snapshot:" in validate


def test_new_product_pack_sources_compile() -> None:
    for relative in (
        "app/product_pack.py",
        "app/product_input.py",
        "app/resolver_pipeline.py",
        "makro_product_pack_workflow.py",
        "gui/product_input_window.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        compile(source, relative, "exec")
