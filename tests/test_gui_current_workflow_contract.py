from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / "makro_gui_workflow.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "gui" / "readonly_runner.py").read_text(encoding="utf-8")
WINDOW = (ROOT / "gui" / "workflow_console_window.py").read_text(encoding="utf-8")
RESULTS = (ROOT / "gui" / "result_loader.py").read_text(encoding="utf-8")
REAL = (ROOT / "gui" / "real_execution.py").read_text(encoding="utf-8")


def test_gui_workflow_uses_current_step1_step2_backend_without_category_hardcode() -> None:
    assert "infer_listing_bootstrap" in WORKFLOW
    assert "select_vertical(page, provider, hints)" in WORKFLOW
    assert "select_brand(page, provider, hints)" in WORKFLOW
    assert "is_product_info_step" in WORKFLOW
    assert "vehicle_camera_system" not in WORKFLOW
    assert "--vertical" not in RUNNER
    assert "--brand" not in RUNNER


def test_gui_step3_runs_current_cold_hot_resolver_then_read_only_fill_plan() -> None:
    assert '"02-cold-resolver"' in WORKFLOW
    assert '"03-hot-resolver"' in WORKFLOW
    assert "_resolver_command(args, live_schema, cold_root)" in WORKFLOW
    assert "_resolver_command(args, live_schema, hot_root)" in WORKFLOW
    assert '"04-fill-plan"' in WORKFLOW
    assert '"makro_plan_listing.py"' in WORKFLOW
    assert "makro_execute_listing.py" not in WORKFLOW
    assert 'writes_performed": 0' in WORKFLOW
    assert 'save_clicked": False' in WORKFLOW
    assert 'send_to_qc_clicked": False' in WORKFLOW


def test_gui_runner_uses_current_provider_defaults_and_one_workflow_process() -> None:
    assert 'api_key_env: str = "AI_API_KEY"' in RUNNER
    assert 'local_model: str = "qwen3.7-plus"' in RUNNER
    assert 'fact_model: str = "qwen3.7-max"' in RUNNER
    assert 'web_model: str = "qwen3.7-max"' in RUNNER
    assert '"makro_gui_workflow.py"' in RUNNER
    assert '"--structured-mode"' in RUNNER
    assert '"json_object"' in RUNNER
    assert '"--disable-thinking"' in RUNNER
    assert '"makro_resolve_ai.py"' not in RUNNER
    assert '"makro_plan_listing.py"' not in RUNNER
    assert "DASHSCOPE_API_KEY" not in RUNNER


def test_gui_exposes_independent_stage_tests_and_full_flow() -> None:
    assert 'QPushButton("① Step 1 · Vertical")' in WINDOW
    assert 'QPushButton("② Step 2 · Brand")' in WINDOW
    assert 'QPushButton("③ Step 3 · Resolve")' in WINDOW
    assert 'self.start_button.setText("④ 完整流程准备")' in WINDOW
    assert 'self.runner.start(config, mode=mode)' in WINDOW
    assert 'self._start_mode("full")' in WINDOW
    assert "self.vertical_input.clear()" in WINDOW
    assert "self.vertical_input.setReadOnly(True)" in WINDOW


def test_real_fill_reuses_current_step3_artifacts_and_canonical_executor() -> None:
    assert 'latest_live_schema(run_dir)' in REAL
    assert 'latest_resolver_manifest(run_dir, "03-hot-resolver")' in REAL
    assert 'run_dir / "04-fill-plan"' in REAL
    assert '"makro_execute_listing.py"' in REAL
    assert '"--decision-packet"' in REAL
    assert '"--supplier-snapshot"' in REAL
    assert '"--send-to-qc"' not in REAL
    assert 'send_to_qc=False (repository policy lock)' in REAL


def test_result_loader_understands_current_resolver_manifest_sections() -> None:
    assert 'manifest.get("product_facts")' in RESULTS
    assert 'manifest.get("best_effort_inference")' in RESULTS
    assert 'manifest.get("web_fill")' in RESULTS
    assert 'manifest.get("final_decision_summary")' in RESULTS
    assert '"cold_resolver_manifest"' in RESULTS
    assert '"resolver_manifest"' in RESULTS


def test_removed_legacy_architecture_is_not_reintroduced_by_gui_workflow() -> None:
    combined = WORKFLOW + RUNNER + WINDOW + RESULTS + REAL
    assert "field_mapping" not in combined
    assert "product_profile" not in combined
