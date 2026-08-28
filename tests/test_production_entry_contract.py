from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
WORKER = (ROOT / "run_packaged_worker.py").read_text(encoding="utf-8")
GUI = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
LEGACY = (ROOT / "main.py").read_text(encoding="utf-8")
BATCH = (ROOT / "makro_batch_job.py").read_text(encoding="utf-8")
EXECUTOR = (ROOT / "makro_execute_listing.py").read_text(encoding="utf-8")


def test_ci_distinguishes_current_production_contract_from_legacy_mock_e2e() -> None:
    assert "production-contract:" in CI
    assert "legacy-mock-e2e:" in CI
    assert "tests/test_production_entry_contract.py" in CI
    assert "tests/test_ai_first_architecture.py" in CI
    assert "tests/test_batch_architecture_contract.py" in CI
    assert "python run_packaged_worker.py --self-test" in CI
    assert "Run legacy browser automation dry-run" in CI


def test_packaged_worker_routes_current_production_helpers_not_legacy_main() -> None:
    for helper in (
        '"workflow": ("makro_gui_workflow.py", workflow_main)',
        '"batch-job": ("makro_batch_job.py", batch_job_main)',
        '"resolve-ai": ("makro_resolve_ai.py", resolve_ai_main)',
        '"execute": ("makro_execute_listing.py", execute_main)',
    ):
        assert helper in WORKER
    assert "from main import" not in WORKER
    assert '"main.py"' not in WORKER


def test_formal_gui_boots_current_product_input_workflow() -> None:
    assert "from gui.product_input_window import ProductInputWorkflowMainWindow" in GUI
    assert "MainWindow = ProductInputWorkflowMainWindow" in GUI
    assert "install_frozen_process_router(window)" in GUI
    assert "install_managed_makro_browser(window)" in GUI


def test_legacy_main_is_explicitly_mock_only_and_not_a_production_dependency() -> None:
    assert "批量自动填写原型" in LEGACY
    assert "MockPlatformAdapter" in LEGACY
    for source in (WORKER, GUI, BATCH, EXECUTOR):
        assert "MockPlatformAdapter" not in source
        assert "from app.runner import AutomationRunner" not in source


def test_batch_prepare_and_executor_keep_write_boundary_explicit() -> None:
    assert "batch preparation is read-only" in BATCH
    assert '"writes_performed": 0' in BATCH
    assert '"save_clicked": False' in BATCH
    assert '"send_to_qc_clicked": False' in BATCH
    assert '"--allow-section-save"' in EXECUTOR
    assert '"send_to_qc_clicked": False' in EXECUTOR
