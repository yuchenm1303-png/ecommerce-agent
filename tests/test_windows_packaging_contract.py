from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "app" / "runtime_paths.py").read_text(encoding="utf-8")
ROUTER = (ROOT / "gui" / "frozen_process_router.py").read_text(encoding="utf-8")
WORKER = (ROOT / "run_packaged_worker.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "EcommerceAgent.spec").read_text(encoding="utf-8")
INSTALLER = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")


def test_packaging_python_sources_compile() -> None:
    for path, source in (
        (ROOT / "app" / "runtime_paths.py", RUNTIME),
        (ROOT / "gui" / "frozen_process_router.py", ROUTER),
        (ROOT / "run_packaged_worker.py", WORKER),
        (ROOT / "run_local_gui.py", RUN),
    ):
        compile(source, str(path), "exec")


def test_frozen_runtime_moves_mutable_state_out_of_install_dir() -> None:
    assert 'os.getenv("LOCALAPPDATA"' in RUNTIME
    assert '"ECOMMERCE_AGENT_DATA_DIR"' in RUNTIME
    assert '"EcommerceAgent"' in RUNTIME
    assert '("logs", "browser_profiles")' in RUNTIME
    assert "else:\n        root = source_project_root()" in RUNTIME


def test_frozen_gui_routes_only_canonical_internal_children_to_worker() -> None:
    assert '"EcommerceAgentWorker.exe"' in ROUTER
    for script in (
        "makro_gui_workflow.py",
        "makro_execute_listing.py",
        "makro_batch_source.py",
        "makro_batch_job.py",
        "makro_resolve_ai.py",
        "makro_plan_listing.py",
        "makro_one_link.py",
    ):
        assert f'"{script}"' in ROUTER
    assert "if not frozen_now:" in ROUTER
    assert 'setattr(module, "QProcess", RoutedQProcess)' in ROUTER
    assert "for module in (readonly_runner, real_execution, batch_runner):" in ROUTER


def test_worker_dispatches_existing_business_entrypoints_and_self_tests_playwright() -> None:
    for helper in (
        "workflow",
        "execute",
        "batch-source",
        "batch-job",
        "resolve-ai",
        "plan-listing",
        "one-link",
    ):
        assert f'"{helper}"' in WORKER
    assert 'if argv[0] == "--self-test":' in WORKER
    assert "from playwright.sync_api import sync_playwright" in WORKER
    assert "sys.argv = [script_name, *rest]" in WORKER


def test_gui_uses_runtime_root_and_installs_router_before_browser_wrappers() -> None:
    assert "from app.runtime_paths import runtime_root" in RUN
    assert "window = MainWindow(runtime_root())" in RUN
    assert "from gui.frozen_process_router import install_frozen_process_router" in RUN
    route = RUN.index("install_frozen_process_router(window)")
    browser = RUN.index("install_managed_makro_browser(window)")
    show = RUN.index("shell.show()")
    assert route < browser < show


def test_pyinstaller_build_is_onedir_with_windowed_gui_and_console_worker() -> None:
    assert 'name="EcommerceAgent"' in SPEC
    assert 'name="EcommerceAgentWorker"' in SPEC
    assert "console=False" in SPEC
    assert "console=True" in SPEC
    assert 'collect_all("playwright")' in SPEC
    assert '(str(ROOT / "gui" / "assets"), "gui/assets")' in SPEC
    assert 'name="EcommerceAgent"' in SPEC[SPEC.index("coll = COLLECT"):]


def test_installer_is_stable_per_user_upgrade_and_does_not_own_runtime_data() -> None:
    assert "AppId={{84E09CC8-51F4-4409-BC73-B5EBC9A4D84A}" in INSTALLER
    assert "DefaultDirName={localappdata}\\Programs\\EcommerceAgent" in INSTALLER
    assert "PrivilegesRequired=lowest" in INSTALLER
    assert "UsePreviousAppDir=yes" in INSTALLER
    assert "EcommerceAgentWorker.exe" not in INSTALLER  # copied by wildcard, not user-facing
    assert "browser_profiles" not in INSTALLER
    assert "logs\\" not in INSTALLER


def test_one_command_build_emits_worker_verified_installer_and_portable_zip() -> None:
    assert "python -m PyInstaller" in BUILD
    assert "EcommerceAgentWorker.exe" in BUILD
    assert "--self-test" in BUILD
    assert "Compress-Archive" in BUILD
    assert "EcommerceAgent-Setup-$Version.exe" in BUILD
    assert "ISCC.exe" in BUILD
