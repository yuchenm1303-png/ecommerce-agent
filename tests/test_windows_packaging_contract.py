from __future__ import annotations

from pathlib import Path

from app.app_branding import application_icon_bytes


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "app" / "runtime_paths.py").read_text(encoding="utf-8")
BRANDING = (ROOT / "app" / "app_branding.py").read_text(encoding="utf-8")
ICON_DATA = (ROOT / "app" / "app_icon_data.py").read_text(encoding="utf-8")
ICON_GENERATOR = (ROOT / "scripts" / "generate_app_icon.py").read_text(encoding="utf-8")
ROUTER = (ROOT / "gui" / "frozen_process_router.py").read_text(encoding="utf-8")
WORKER = (ROOT / "run_packaged_worker.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "EcommerceAgent.spec").read_text(encoding="utf-8")
INSTALLER = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
BUILD_REQUIREMENTS = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")


def test_packaging_python_sources_compile() -> None:
    for path, source in (
        (ROOT / "app" / "runtime_paths.py", RUNTIME),
        (ROOT / "app" / "app_branding.py", BRANDING),
        (ROOT / "app" / "app_icon_data.py", ICON_DATA),
        (ROOT / "scripts" / "generate_app_icon.py", ICON_GENERATOR),
        (ROOT / "gui" / "frozen_process_router.py", ROUTER),
        (ROOT / "run_packaged_worker.py", WORKER),
        (ROOT / "run_local_gui.py", RUN),
    ):
        compile(source, str(path), "exec")


def test_approved_application_icon_is_embedded_and_integrity_checked() -> None:
    raw = application_icon_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(raw) > 5_000
    assert "APP_ICON_PNG_BASE64" in ICON_DATA
    assert "APP_ICON_SHA256" in ICON_DATA
    assert "hashlib.sha256(raw).hexdigest()" in BRANDING
    assert 'pixmap.loadFromData(application_icon_bytes(), "PNG")' in BRANDING
    assert "apply_qt_application_icon(app)" in RUN
    assert "SetCurrentProcessExplicitAppUserModelID" in BRANDING


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


def test_pyinstaller_build_is_onedir_with_windowed_gui_console_worker_and_icon() -> None:
    assert 'name="EcommerceAgent"' in SPEC
    assert 'name="EcommerceAgentWorker"' in SPEC
    assert "console=False" in SPEC
    assert "console=True" in SPEC
    assert 'collect_all("playwright")' in SPEC
    assert '(str(ROOT / "gui" / "assets"), "gui/assets")' in SPEC
    assert 'APP_ICON = ROOT / "packaging" / "app_icon.ico"' in SPEC
    assert SPEC.count("icon=str(APP_ICON)") == 2
    assert 'name="EcommerceAgent"' in SPEC[SPEC.index("coll = COLLECT"):]


def test_installer_is_stable_per_user_upgrade_and_uses_branded_icon() -> None:
    assert "AppId={{84E09CC8-51F4-4409-BC73-B5EBC9A4D84A}" in INSTALLER
    assert "DefaultDirName={localappdata}\\Programs\\EcommerceAgent" in INSTALLER
    assert "PrivilegesRequired=lowest" in INSTALLER
    assert "UsePreviousAppDir=yes" in INSTALLER
    assert "SetupIconFile={#IconFile}" in INSTALLER
    assert "AppVerName={#MyAppName} {#AppVersion}" in INSTALLER
    assert "VersionInfoCompany=ecommerce-agent" in INSTALLER
    assert "VersionInfoDescription={#MyAppName} Setup" in INSTALLER
    assert "VersionInfoProductName={#MyAppName}" in INSTALLER
    assert "VersionInfoVersion={#AppVersion}" in INSTALLER
    assert "VersionInfoProductVersion={#AppVersion}" in INSTALLER
    assert "VersionInfoProductTextVersion={#AppVersion}" in INSTALLER
    assert '#define InstalledIconName "EcommerceAgent.ico"' in INSTALLER
    assert '#define InstalledIconName "EcommerceAgent-" + AppVersion + ".ico"' not in INSTALLER
    assert 'Source: "{#IconFile}"; DestDir: "{app}\\icons"; DestName: "{#InstalledIconName}"' in INSTALLER
    assert 'IconFilename: "{app}\\icons\\{#InstalledIconName}"' in INSTALLER
    assert "UninstallDisplayIcon={app}\\icons\\{#InstalledIconName}" in INSTALLER
    assert 'Type: files; Name: "{app}\\icons\\EcommerceAgent-*.ico"' in INSTALLER
    assert 'Check: ShouldCreateDesktopShortcut' in INSTALLER
    assert "function ShouldCreateDesktopShortcut: Boolean;" in INSTALLER
    assert "WizardIsTaskSelected('desktopicon')" in INSTALLER
    assert "FileExists(ExpandConstant('{autodesktop}\\EcommerceAgent Listing Studio.lnk'))" in INSTALLER
    assert 'IconIndex: 0; Tasks: desktopicon' not in INSTALLER
    assert "EcommerceAgentWorker.exe" not in INSTALLER  # copied by wildcard, not user-facing
    assert "browser_profiles" not in INSTALLER
    assert "logs\\" not in INSTALLER


def test_one_command_build_generates_icon_and_emits_verified_packages() -> None:
    assert "Pillow" in BUILD_REQUIREMENTS
    assert "generate_app_icon.py" in BUILD
    assert 'packaging\\app_icon.ico' in BUILD
    assert '"/DIconFile=$IconFile"' in BUILD
    for size in (16, 24, 32, 48, 64, 128, 256):
        assert f"({size}, {size})" in ICON_GENERATOR
    assert "python -m PyInstaller" in BUILD
    assert "EcommerceAgentWorker.exe" in BUILD
    assert "--self-test" in BUILD
    assert "Compress-Archive" in BUILD
    assert "EcommerceAgent-Setup-$Version.exe" in BUILD
    assert "ISCC.exe" in BUILD
    assert "Updater.spec" in BUILD
    assert 'updater\\updater.exe' in BUILD
    assert "--self-check" in (
        ROOT / ".github" / "workflows" / "windows-package.yml"
    ).read_text(encoding="utf-8")


def test_standalone_updater_is_a_windowed_single_file_build() -> None:
    updater_spec = (ROOT / "packaging" / "Updater.spec").read_text(encoding="utf-8")
    entry = (ROOT / "scripts" / "updater_main.py").read_text(encoding="utf-8")
    core = (ROOT / "app" / "updater_core.py").read_text(encoding="utf-8")
    assert 'name="updater"' in updater_spec
    assert "console=False" in updater_spec
    assert "exclude_binaries=False" in updater_spec
    assert "updater_main.py" in updater_spec
    assert "--job" in entry
    assert "--self-check" in entry
    assert "import ctypes" in core
    assert "subprocess" in core
