from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = (ROOT / "native" / "installer-bootstrapper" / "main.cpp").read_text(encoding="utf-8")
PROJECT = (ROOT / "native" / "installer-bootstrapper" / "EcommerceAgentInstaller.vcxproj").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
WINDOWS = (ROOT / ".github" / "workflows" / "windows-package.yml").read_text(encoding="utf-8")


def test_customer_installer_is_a_native_single_exe_bootstrapper() -> None:
    assert "FindResourceW" in BOOTSTRAP
    assert "RT_RCDATA" in BOOTSTRAP
    assert "CreateProcessW" in BOOTSTRAP
    assert "forwarded_command_line" in BOOTSTRAP
    assert '<SubSystem>Windows</SubSystem>' in PROJECT
    assert '<ResourceCompile Include="$(PayloadResourceFile)"' in PROJECT


def test_bootstrapper_repairs_only_broken_velopack_install_root() -> None:
    assert 'kDefaultInstallDir[] = L"Smirel.ListingStudio"' in BOOTSTRAP
    assert 'root / L"Update.exe"' in BOOTSTRAP
    assert 'root / L"current" / kMainExe' in BOOTSTRAP
    assert "directory_has_entries(install_root) && !is_complete_install(install_root)" in BOOTSTRAP
    assert "fs::rename(install_root, stale_backup" in BOOTSTRAP
    assert "restore_stale_install(install_root, stale_backup)" in BOOTSTRAP
    assert "EcommerceAgent_DATA_DIR" not in BOOTSTRAP


def test_build_embeds_native_velopack_setup_into_customer_installer() -> None:
    assert 'native\\installer-bootstrapper\\EcommerceAgentInstaller.vcxproj' in BUILD
    assert '"101 RCDATA `"$NativeSetupRcPath`""' in BUILD
    assert 'Copy-Item $InstallerWrapperExe $SetupAlias -Force' in BUILD
    assert 'Invoke-CustomerInstallerSigning' in BUILD
    assert 'Copy-Item $NativeSetup.FullName $SetupAlias -Force' not in BUILD


def test_windows_smoke_seeds_broken_install_before_customer_setup() -> None:
    assert "orphaned-velopack-state.txt" in WINDOWS
    assert '"--silent", "--installto", $installDir' in WINDOWS
