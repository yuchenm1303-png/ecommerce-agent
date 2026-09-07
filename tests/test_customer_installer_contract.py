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


def test_bootstrapper_repairs_only_owned_default_velopack_root() -> None:
    assert 'kDefaultInstallDir[] = L"Smirel.ListingStudio"' in BOOTSTRAP
    assert "struct InstallTarget" in BOOTSTRAP
    assert "owns_default_root" in BOOTSTRAP
    assert 'return {fs::path(argv[i + 1]), false};' in BOOTSTRAP
    assert "return {default_install_root(), true};" in BOOTSTRAP
    assert "target.owns_default_root" in BOOTSTRAP
    assert "install_root_has_state(target.root)" in BOOTSTRAP
    assert "fs::rename(target.root, stale_backup" in BOOTSTRAP
    assert "restore_stale_install(target.root, stale_backup)" in BOOTSTRAP
    assert "EcommerceAgent_DATA_DIR" not in BOOTSTRAP


def test_complete_install_requires_files_and_matching_velopack_registration() -> None:
    assert 'root / L"Update.exe"' in BOOTSTRAP
    assert 'root / L"current" / kMainExe' in BOOTSTRAP
    assert 'root / L"current" / L"sq.version"' in BOOTSTRAP
    assert "uninstall_registration_matches(root)" in BOOTSTRAP
    assert 'CurrentVersion\\\\Uninstall\\\\Smirel.ListingStudio' in BOOTSTRAP
    assert 'RegGetValueW(key, nullptr, L"InstallLocation"' in BOOTSTRAP
    assert "paths_equal_case_insensitive" in BOOTSTRAP
    assert "advapi32.lib" in PROJECT


def test_build_embeds_native_velopack_setup_into_customer_installer() -> None:
    assert 'native\\installer-bootstrapper\\EcommerceAgentInstaller.vcxproj' in BUILD
    assert '"101 RCDATA `"$NativeSetupRcPath`""' in BUILD
    assert 'Copy-Item $InstallerWrapperExe $SetupAlias -Force' in BUILD
    assert 'Invoke-CustomerInstallerSigning' in BUILD
    assert 'Copy-Item $NativeSetup.FullName $SetupAlias -Force' not in BUILD


def test_windows_smoke_uses_real_default_install_root_for_stale_recovery() -> None:
    assert 'Join-Path $env:LOCALAPPDATA "Smirel.ListingStudio"' in WINDOWS
    assert "orphaned-velopack-state.txt" in WINDOWS
    assert 'ArgumentList @("--silent")' in WINDOWS
    assert '"--installto", $installDir' not in WINDOWS
