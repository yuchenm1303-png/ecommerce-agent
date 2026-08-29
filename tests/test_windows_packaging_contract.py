from __future__ import annotations

import importlib
import json
from pathlib import Path

from app.app_branding import application_icon_bytes
from gui.frozen_process_router import route_process_start

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "app" / "runtime_paths.py").read_text(encoding="utf-8")
VELOPACK_RUNTIME = (ROOT / "app" / "velopack_runtime.py").read_text(encoding="utf-8")
RUNTIME_HOOK = (ROOT / "packaging" / "velopack_runtime_hook.py").read_text(encoding="utf-8")
BRANDING = (ROOT / "app" / "app_branding.py").read_text(encoding="utf-8")
ICON_DATA = (ROOT / "app" / "app_icon_data.py").read_text(encoding="utf-8")
ICON_GENERATOR = (ROOT / "scripts" / "generate_app_icon.py").read_text(encoding="utf-8")
ROUTER = (ROOT / "gui" / "frozen_process_router.py").read_text(encoding="utf-8")
WORKER = (ROOT / "run_packaged_worker.py").read_text(encoding="utf-8")
OWNED_EXECUTOR = (ROOT / "makro_execute_owned.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
QUICK_BATCH = (ROOT / "gui" / "quick_batch_list.py").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "EcommerceAgent.spec").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
WINDOWS = (ROOT / ".github" / "workflows" / "windows-package.yml").read_text(encoding="utf-8")


def test_packaging_python_sources_compile() -> None:
    for path, source in (
        (ROOT / "app" / "runtime_paths.py", RUNTIME),
        (ROOT / "app" / "velopack_runtime.py", VELOPACK_RUNTIME),
        (ROOT / "packaging" / "velopack_runtime_hook.py", RUNTIME_HOOK),
        (ROOT / "makro_execute_owned.py", OWNED_EXECUTOR),
        (ROOT / "run_local_gui.py", RUN),
        (ROOT / "gui" / "quick_batch_list.py", QUICK_BATCH),
    ):
        compile(source, str(path), "exec")


def test_quick_batch_role_initialization_is_import_safe() -> None:
    assert "_ROLES = {name: _BASE + index + 1" not in QUICK_BATCH
    assert "name: int(Qt.ItemDataRole.UserRole) + index + 1" in QUICK_BATCH
    module = importlib.import_module("gui.quick_batch_list")
    assert module.QuickBatchList._ROLES
    assert module.QuickBatchList._ROLE_KEYS


def test_approved_application_icon_is_preserved() -> None:
    raw = application_icon_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(raw) > 5_000
    assert "APP_ICON_PNG_BASE64" in ICON_DATA
    assert "hashlib.sha256(raw).hexdigest()" in BRANDING
    assert "apply_qt_application_icon(app)" in RUN


def test_windows_icon_pipeline_emits_and_validates_full_shell_size_pyramid() -> None:
    for edge in (16, 24, 32, 48, 64, 128, 256):
        assert f"({edge}, {edge})" in ICON_GENERATOR
    assert "ICON_MASTER_SIZE = max" in ICON_GENERATOR
    assert "_icon_master(source).save(output, format=\"ICO\", sizes=ICON_SIZES)" in ICON_GENERATOR
    assert "generated.info.get(\"sizes\")" in ICON_GENERATOR
    assert "generated_sizes != expected_sizes" in ICON_GENERATOR
    assert "_validate_icon(output)" in ICON_GENERATOR
    assert "icon=str(APP_ICON)" in SPEC
    assert '"--icon", $IconFile' in BUILD


def test_frozen_runtime_keeps_mutable_state_outside_versioned_current_dir() -> None:
    assert 'os.getenv("LOCALAPPDATA"' in RUNTIME
    assert '"ECOMMERCE_AGENT_DATA_DIR"' in RUNTIME
    assert '("logs", "browser_profiles")' in RUNTIME
    assert "velopack_root" in RUNTIME
    assert "Software\\EcommerceAgent" not in RUNTIME


def test_gui_worker_routing_contract_remains_unchanged() -> None:
    assert '"EcommerceAgentWorker.exe"' in ROUTER
    assert '"makro_execute_owned.py"' in ROUTER
    assert '"execute-owned": ("makro_execute_owned.py", execute_owned_main)' in WORKER
    assert 'if argv[0] == "--self-test":' in WORKER
    assert "from playwright.sync_api import sync_playwright" in WORKER


def test_frozen_owned_execute_routes_to_worker_exe_not_gui_exe(tmp_path: Path) -> None:
    gui = tmp_path / "EcommerceAgent.exe"
    worker = tmp_path / "EcommerceAgentWorker.exe"
    program, args = route_process_start(
        str(gui),
        ["makro_execute_owned.py", "--help"],
        frozen=True,
        current_executable=str(gui),
        worker_executable=str(worker),
    )
    assert Path(program) == worker.resolve()
    assert args == ["makro_execute_owned.py", "--help"]


def test_pyinstaller_is_onedir_and_embeds_velopack_runtime_hook() -> None:
    assert 'name="EcommerceAgent"' in SPEC
    assert 'name="EcommerceAgentWorker"' in SPEC
    assert "console=False" in SPEC
    assert "console=True" in SPEC
    assert 'collect_all("velopack")' in SPEC
    assert 'runtime_hooks=[str(VELOPACK_RUNTIME_HOOK)]' in SPEC
    assert 'name="EcommerceAgent"' in SPEC[SPEC.index("coll = COLLECT"):]
    assert "onefile" not in SPEC.lower()


def test_velopack_toolchain_is_pinned_and_build_replaces_inno() -> None:
    manifest = json.loads((ROOT / ".config" / "dotnet-tools.json").read_text(encoding="utf-8"))
    assert manifest["tools"]["vpk"]["version"] == "1.2.0"
    assert "dotnet tool restore" in BUILD
    assert "dotnet tool run vpk -- @PackArgs" in BUILD
    assert '"pack"' in BUILD
    assert '"--packId", "EcommerceAgent"' in BUILD
    assert '"--mainExe", "EcommerceAgent.exe"' in BUILD
    assert '"--runtime", "win-x64"' in BUILD
    assert "ISCC.exe" not in BUILD
    assert "Updater.spec" not in BUILD
    assert "Compress-Archive" not in BUILD


def test_windows_ci_smokes_canonical_velopack_layout_and_uninstall() -> None:
    assert "actions/setup-dotnet@v4" in WINDOWS
    assert "dotnet tool restore" in WINDOWS
    assert '"--silent", "--installto", $installDir' in WINDOWS
    assert 'Join-Path $installDir "Update.exe"' in WINDOWS
    assert 'Join-Path $installDir "current\\EcommerceAgent.exe"' in WINDOWS
    assert '& $worker makro_execute_owned.py --help' in WINDOWS
    assert "Installed owned-execute worker dispatch failed" in WINDOWS
    assert 'ArgumentList @("--silent", "uninstall")' in WINDOWS
    assert "Velopack uninstall left installation root behind" in WINDOWS
    assert "Inno Setup" not in WINDOWS


def test_build_discovers_native_assets_and_never_guesses_velopack_package_names() -> None:
    assert 'EcommerceAgent-Setup-$Version.exe' in BUILD
    assert 'EcommerceAgent-$Version-portable.zip' in BUILD
    assert "Get-SingleVelopackArtifact" in BUILD
    assert "Resolve-VelopackFullPackage" in BUILD
    assert '-Filter "$PackId*-Setup.exe"' in BUILD
    assert '-Filter "$PackId*-Portable.zip"' in BUILD
    assert 'releases.$Channel.json' in BUILD
    assert '[string]$Target[0].FileName' in BUILD
    assert '[IO.Path]::GetFileName($FileName) -ne $FileName' in BUILD
    assert 'Join-Path $VelopackDir "$PackId-Setup.exe"' not in BUILD
    assert 'Join-Path $VelopackDir "$PackId-Portable.zip"' not in BUILD
    assert '"$PackId-$Version-full.nupkg"' not in BUILD


def test_build_validates_feed_binding_and_has_production_signing_hooks() -> None:
    assert "$Feed.Assets" in BUILD
    assert '"Full"' in BUILD
    assert "release index/package size mismatch" in BUILD
    assert "VPK_AZURE_TRUSTED_SIGN_FILE" in BUILD
    assert "VPK_SIGN_PARAMS" in BUILD
