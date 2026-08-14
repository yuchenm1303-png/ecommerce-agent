from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTER = (ROOT / "gui" / "frozen_process_router.py").read_text(encoding="utf-8")
WORKER = (ROOT / "run_packaged_worker.py").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "EcommerceAgent.spec").read_text(encoding="utf-8")


def test_frozen_gui_routes_product_pack_workflow_to_worker() -> None:
    assert '"makro_product_pack_workflow.py"' in ROUTER
    assert "_HELPER_SCRIPT_NAMES" in ROUTER
    assert "RoutedQProcess" in ROUTER


def test_packaged_worker_imports_and_registers_product_pack_workflow() -> None:
    assert "from makro_product_pack_workflow import main as product_pack_workflow_main" in WORKER
    assert (
        '"product-pack-workflow": '
        '("makro_product_pack_workflow.py", product_pack_workflow_main)'
    ) in WORKER
    assert "_BY_SCRIPT" in WORKER


def test_pyinstaller_worker_analysis_reaches_registered_workflow_by_direct_import() -> None:
    # EcommerceAgentWorker is analyzed from run_packaged_worker.py. The direct
    # import above makes the product-pack workflow and its parser dependencies
    # part of that same dependency graph without a second brittle hidden-import list.
    assert '[str(ROOT / "run_packaged_worker.py")]' in SPEC
    assert "worker_a = Analysis(" in SPEC
    assert "hiddenimports=playwright_hiddenimports" in SPEC


def test_packaging_contract_sources_compile() -> None:
    compile(ROUTER, "gui/frozen_process_router.py", "exec")
    compile(WORKER, "run_packaged_worker.py", "exec")
