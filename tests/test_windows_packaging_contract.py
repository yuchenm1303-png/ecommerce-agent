from __future__ import annotations

import json
from pathlib import Path

from app.app_branding import application_icon_bytes

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "app" / "runtime_paths.py").read_text(encoding="utf-8")
VELOPACK_RUNTIME = (ROOT / "app" / "velopack_runtime.py").read_text(encoding="utf-8")
RUNTIME_HOOK = (ROOT / "packaging" / "velopack_runtime_hook.py").read_text(encoding="utf-8")
BRANDING = (ROOT / "app" / "app_branding.py").read_text(encoding="utf-8")
ICON_DATA = (ROOT / "app" / "app_icon_data.py").read_text(encoding="utf-8")
ICON_GENERATOR = (ROOT / "scripts" / "generate_app_icon.py").read_text(encoding="utf-8")
MSI_SMOKE = (ROOT / "scripts" / "test_velopack_msi.ps1").read_text(encoding="utf-8")
ROUTER = (ROOT / "gui" / "frozen_process_router.py").read_text(encoding="utf-8")
NATIVE_SHELL = (ROOT / "gui" / "native_window_shell.py").read_text(encoding="utf-8")
UPDATE_PANEL = (ROOT / "gui" / "update_panel.py").read_text(encoding="utf-8")
WORKER = (ROOT / "run_packaged_worker.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "EcommerceAgent.spec").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
WINDOWS = (ROOT / ".github" / "workflows" / "windows-package.yml").read_text(encoding="utf-8")


def test_packaging_python_sources_compile() -> None:
    for path, source in (
        (ROOT / "app" / "runtime_paths.py", RUNTIME),
        (ROOT / "app" / "velopack_runtime.py", VELOPACK_RUNTIME),
        (ROOT / "packaging" / "velopack_runtime_hook.py", RUNTIME_HOOK),
        (ROOT / "gui" / "update_panel.py", UPDATE_PANEL),
        (ROOT / "gui" / "native_window_shell.py", NATIVE_SHELL),
        (ROOT / "run_local_gui.py", RUN),
    ):
        compile(source, str(path), "exec")


def test_approved_application_icon_is_preserved_everywhere() -> None:
    raw = application_icon_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(raw) > 5_000
    assert "APP_ICON_PNG_BASE64" in ICON_DATA
    assert "hashlib.sha256(raw).hexdigest()" in BRANDING
    assert 'APP_USER_MODEL_ID = "Smirel.ListingStudio"' in BRANDING
    assert "apply_qt_application_icon(app)" in RUN
    assert "owner.setIcon(app_icon)" in NATIVE_SHELL
    assert "overlay.setWindowIcon(app_icon)" in NATIVE_SHELL


def test_branding_generator_builds_setup_and_msi_artwork() -> None:
    assert "build_installer_splash" in ICON_GENERATOR
    assert "build_msi_banner" in ICON_GENERATOR
    assert "build_msi_logo" in ICON_GENERATOR
    assert '(493, 58)' in ICON_GENERATOR
    assert '(493, 312)' in ICON_GENERATOR
    assert 'format="BMP"' in ICON_GENERATOR
    assert 'format="PNG"' in ICON_GENERATOR


def test_frozen_runtime_keeps_mutable_state_outside_versioned_current_dir() -> None:
    assert 'os.getenv("LOCALAPPDATA"' in RUNTIME
    assert '"ECOMMERCE_AGENT_DATA_DIR"' in RUNTIME
    assert '("logs", "browser_profiles")' in RUNTIME
    assert "velopack_root" in RUNTIME
    assert "Software\\EcommerceAgent" not in RUNTIME


def test_gui_worker_routing_contract_remains_unchanged() -> None:
    assert '"EcommerceAgentWorker.exe"' in ROUTER
    assert 'if argv[0] == "--self-test":' in WORKER
    assert "from playwright.sync_api import sync_playwright" in WORKER


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
    assert '"--packId", $PackId' in BUILD
    assert '"--mainExe", "EcommerceAgent.exe"' in BUILD
    assert '"--runtime", "win-x64"' in BUILD
    assert "ISCC.exe" not in BUILD
    assert "Updater.spec" not in BUILD
    assert "Compress-Archive" not in BUILD


def test_velopack_pack_uses_canonical_branding_and_msi() -> None:
    assert '"--icon", $IconFile' in BUILD
    assert '"--splashImage", $SplashFile' in BUILD
    assert '"--splashProgressColor", "#5DA7FF"' in BUILD
    assert '"--aumid", $PackId' in BUILD
    assert '"--shortcuts", "Desktop,StartMenuRoot"' in BUILD
    assert '"--msi", "true"' in BUILD
    assert '"--instLocation", "PerUser"' in BUILD
    assert '"--msiBanner", $MsiBannerFile' in BUILD
    assert '"--msiLogo", $MsiLogoFile' in BUILD
    assert 'EcommerceAgent-Setup-$Version.msi' in BUILD


def test_windows_ci_uses_one_isolated_pinned_velopack_msi_smoke() -> None:
    assert "actions/setup-dotnet@v4" in WINDOWS
    assert "dotnet tool restore" in WINDOWS
    assert '"--silent", "--installto", $installDir' in WINDOWS
    assert 'Join-Path $installDir "Update.exe"' in WINDOWS
    assert 'Join-Path $installDir "current\\EcommerceAgent.exe"' in WINDOWS
    assert 'scripts\\test_velopack_msi.ps1' in WINDOWS
    assert WINDOWS.index("Silent branded MSI install and uninstall smoke test") < WINDOWS.index(
        "Silent Velopack Setup smoke test"
    )
    assert 'VELOPACK_INSTALLDIR=`"$installDir`"' in MSI_SMOKE
    assert "supported public install" in MSI_SMOKE
    assert 'Uninstall\\MSI:$PackId' in MSI_SMOKE
    assert 'Uninstall\\$PackId' in MSI_SMOKE
    assert '"/i' in MSI_SMOKE
    assert '"/x' in MSI_SMOKE
    assert 'current\\EcommerceAgentWorker.exe' in MSI_SMOKE
    assert 'current\\_internal\\packaging\\VERSION' in MSI_SMOKE
    assert "InstallLocation mismatch" in MSI_SMOKE
    assert "uninstall registration remained" in MSI_SMOKE
    assert "MSI uninstall left installed component" in MSI_SMOKE
    assert "Inno Setup" not in WINDOWS


def test_build_discovers_native_assets_and_never_guesses_velopack_package_names() -> None:
    assert 'EcommerceAgent-Setup-$Version.exe' in BUILD
    assert 'EcommerceAgent-Setup-$Version.msi' in BUILD
    assert 'EcommerceAgent-$Version-portable.zip' in BUILD
    assert "Get-SingleVelopackArtifact" in BUILD
    assert "Resolve-VelopackFullPackage" in BUILD
    assert '-Filter "$PackId*-Setup.exe"' in BUILD
    assert '-Filter "$PackId*.msi"' in BUILD
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
