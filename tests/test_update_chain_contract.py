from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLISH = (ROOT / ".github" / "workflows" / "publish-update.yml").read_text(encoding="utf-8")
WINDOWS = (ROOT / ".github" / "workflows" / "windows-package.yml").read_text(encoding="utf-8")
TEST_BUILD = (ROOT / ".github" / "workflows" / "publish-test-build.yml").read_text(encoding="utf-8")
UPDATER = (ROOT / "gui" / "app_updater.py").read_text(encoding="utf-8")
PRESENTATION = (ROOT / "gui" / "resilient_app_updater.py").read_text(encoding="utf-8")
UPDATE_RUNTIME = (ROOT / "gui" / "update_runtime.py").read_text(encoding="utf-8")
RUNTIME_PATHS = (ROOT / "app" / "runtime_paths.py").read_text(encoding="utf-8")
BROWSER_SESSION = (ROOT / "app" / "browser_session.py").read_text(encoding="utf-8")
BROWSER_MANAGER = (ROOT / "gui" / "browser_session_manager.py").read_text(encoding="utf-8")
BROWSER_GATE = (ROOT / "app" / "update_browser_gate.py").read_text(encoding="utf-8")
LOCK_AUDIT = (ROOT / "app" / "windows_restart_manager.py").read_text(encoding="utf-8")
UPDATER_PANEL = (ROOT / "app" / "updater_panel.py").read_text(encoding="utf-8")
CORE = (ROOT / "app" / "updater_core.py").read_text(encoding="utf-8")
ENTRY = (ROOT / "scripts" / "updater_main.py").read_text(encoding="utf-8")
ENTRY_V2 = (ROOT / "scripts" / "updater_main_v2.py").read_text(encoding="utf-8")
UPDATER_SPEC = (ROOT / "packaging" / "Updater.spec").read_text(encoding="utf-8")
GUI_ENTRY = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
E2E = (ROOT / "scripts" / "test_windows_update_e2e.ps1").read_text(encoding="utf-8")
E2E_PARENT = (ROOT / "tests" / "windows_updater_e2e_parent.py").read_text(encoding="utf-8")

UPDATER_TESTS = (
    "tests/test_windows_packaging_contract.py",
    "tests/test_update_delivery_contract.py",
    "tests/test_updater_core_contract.py",
    "tests/test_updater_browser_shutdown_contract.py",
    "tests/test_update_panel_contract.py",
    "tests/test_resilient_app_updater_contract.py",
    "tests/test_updater_onefile_contract.py",
    "tests/test_update_chain_contract.py",
)


def test_publish_workflow_guards_stable_version_order_and_policy() -> None:
    assert "concurrency:" in PUBLISH
    assert "Latest published Stable version" in PUBLISH
    assert "must be newer than current Stable" in PUBLISH
    assert "Minimum supported version cannot exceed release version" in PUBLISH
    assert "Release version must be strict x.y.z" in PUBLISH
    assert "Minimum supported version must be strict x.y.z" in PUBLISH


def test_publish_manifest_contains_full_binding_metadata() -> None:
    for token in (
        'delivery = "portal"',
        'portal_url = "https://smirel.com/download/"',
        "installer_sha256 = $sha",
        "installer_size = $setupSize",
        'source_commit = "${{ steps.source.outputs.sha }}"',
        'channel = "stable"',
    ):
        assert token in PUBLISH


def test_every_packaging_workflow_runs_full_update_contract_suite() -> None:
    for workflow in (PUBLISH, WINDOWS, TEST_BUILD):
        for test_path in UPDATER_TESTS:
            assert test_path in workflow


def test_windows_package_triggers_on_every_update_runtime_source() -> None:
    for path in (
        "app/runtime_paths.py",
        "app/browser_session.py",
        "app/updater_core.py",
        "app/update_browser_gate.py",
        "app/updater_panel.py",
        "app/windows_restart_manager.py",
        "gui/app_updater.py",
        "gui/browser_session_manager.py",
        "gui/resilient_app_updater.py",
        "gui/update_runtime.py",
        "scripts/updater_main.py",
        "scripts/updater_main_v2.py",
        "scripts/build_windows.ps1",
        "scripts/test_windows_update_e2e.ps1",
        "tests/windows_updater_e2e_parent.py",
        "tests/windows_updater_fake_edge.py",
        "tests/test_updater_core_contract.py",
        "tests/test_updater_browser_shutdown_contract.py",
        "tests/test_update_panel_contract.py",
        "tests/test_update_chain_contract.py",
    ):
        assert f'- "{path}"' in WINDOWS
    assert '- "packaging/**"' in WINDOWS


def test_test_build_smokes_installed_updater_runtime() -> None:
    assert 'updater\\updater.exe' in TEST_BUILD
    assert "& $updater --self-check" in TEST_BUILD
    assert "Installed updater self-check failed" in TEST_BUILD


def test_chain_has_two_sided_handoff_and_post_install_version_gate() -> None:
    assert "JOB_VERSION = 2" in CORE
    assert '"status": "accepted"' in CORE
    assert "_HANDOFF_ACK_TIMEOUT_S" in UPDATER
    assert 'ack.get("status") == "accepted"' in UPDATER
    assert "installed version mismatch" in CORE
    assert "_launch_app(job.app_executable)" in CORE
    assert "_consume_previous_update_result" in UPDATER


def test_updater_self_check_exercises_embedded_core_and_v2_panel_entry() -> None:
    assert "from app.updater_core import JOB_VERSION, UpdaterJob, run_job" in ENTRY
    assert "JOB_VERSION < 2" in ENTRY
    assert "legacy._self_check()" in ENTRY_V2
    assert "NativeUpdatePanel" in ENTRY_V2
    assert "if not panel.ready" in ENTRY_V2
    assert "updater_main_v2.py" in UPDATER_SPEC


def test_update_preflight_owns_managed_browser_without_killing_normal_edge() -> None:
    assert "listener_pid" in BROWSER_GATE
    assert 'OWNED_BROWSER_IMAGE = "msedge.exe"' in BROWSER_GATE
    assert '["taskkill", "/PID", str(pid), "/T", "/F"]' in BROWSER_GATE
    assert "unexpected process" in BROWSER_GATE
    assert "begin_update_quiesce" in PRESENTATION
    assert "wait_for_update_quiesce" in PRESENTATION
    assert "_update_quiesced" in BROWSER_MANAGER
    assert "close_managed_browser" in PRESENTATION
    assert "不会关闭其他普通 Edge 窗口" in PRESENTATION


def test_external_edge_spawn_is_isolated_from_pyinstaller_runtime() -> None:
    assert "fresh_external_child_environment" in BROWSER_SESSION
    assert 'key.startswith("_PYI_")' in BROWSER_SESSION
    assert 'PYINSTALLER_RESET_ENVIRONMENT' in BROWSER_SESSION
    assert "SetDllDirectoryW(None)" in BROWSER_SESSION
    assert "env=env" in BROWSER_SESSION


def test_restart_manager_lock_audit_runs_before_inno() -> None:
    assert "audit_install_tree_locks" in CORE
    assert "install tree lock audit start" in CORE
    assert 'RESULT_FILE_LOCKED = "file_lock_blocked"' in CORE
    assert 'RESULT_LOCK_AUDIT_FAILED = "file_lock_audit_failed"' in CORE
    assert "RmStartSession" in LOCK_AUDIT
    assert "RmRegisterResources" in LOCK_AUDIT
    assert "RmGetList" in LOCK_AUDIT
    assert CORE.index("_install_tree_lock_gate(job)") < CORE.index("running installer:")


def test_update_progress_is_continuous_and_inno_hidden() -> None:
    assert "class _UpdateProgressPanel(QDialog)" in PRESENTATION
    assert "listing-studio-update-sha256" in PRESENTATION
    assert "listing-studio-update-browser-close" in PRESENTATION
    assert '"/VERYSILENT"' in PRESENTATION
    assert "NativeUpdatePanel" in ENTRY_V2
    assert "步骤 5/6" in UPDATER_PANEL
    assert "步骤 6/6" in UPDATER_PANEL
    assert "WindowStaysOnTopHint" in PRESENTATION
    assert "native update panel ready" in UPDATER_PANEL


def test_inno_replaces_immutable_pyinstaller_directories_cleanly() -> None:
    assert 'Type: filesandordirs; Name: "{app}\\_internal"' in INSTALLER
    assert 'Type: filesandordirs; Name: "{app}\\updater"' in INSTALLER


def test_portable_build_cannot_enter_installed_self_update_path() -> None:
    assert 'Subkey: "Software\\EcommerceAgent"' in INSTALLER
    assert 'ValueName: "InstallDir"' in INSTALLER
    assert "def is_installed_distribution()" in RUNTIME_PATHS
    assert "is_installed_distribution()" in GUI_ENTRY
    assert "if not is_frozen() or is_installed_distribution():" in GUI_ENTRY


def test_independent_updater_bootloader_is_reset_before_handoff() -> None:
    assert 'PYINSTALLER_RESET_ENVIRONMENT' in UPDATE_RUNTIME
    assert 'env=_fresh_pyinstaller_child_environment()' in UPDATE_RUNTIME
    assert 'os.environ[_PYINSTALLER_RESET_ENV] = "1"' in UPDATE_RUNTIME
    assert 'PYINSTALLER_RESET_ENVIRONMENT' in ENTRY


def test_stable_e2e_relaunches_real_installed_gui_and_exercises_preflight() -> None:
    assert "ECOMMERCE_AGENT_UPDATE_E2E_MARKER" in GUI_ENTRY
    assert 'app_executable = install_dir / "EcommerceAgent.exe"' in E2E_PARENT
    assert '"app_executable": str(app_executable)' in E2E_PARENT
    assert "close_managed_browser" in E2E_PARENT
    assert 'real-gui-relaunch.json' in E2E_PARENT
    assert 'real-gui-relaunch.json' in E2E
    assert 'windows_updater_fake_edge.py' in E2E
    assert 'native update panel ready' in E2E
    assert 'install tree lock audit clean' in E2E
    assert 'hidden Inno' in E2E
    assert 'real installed GUI' in E2E
    assert 'RelaunchProbe' not in E2E


def test_heavy_e2e_is_release_gate_not_normal_build_tax() -> None:
    assert "[switch]$RunUpdateE2E" in BUILD
    assert '$ShouldRunUpdateE2E = [bool]$RunUpdateE2E -or ($env:GITHUB_WORKFLOW -eq "Publish Update")' in BUILD
    assert "Skipping heavy updater E2E for normal development build" in BUILD
