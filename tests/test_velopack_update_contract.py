from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPDATER = (ROOT / "gui" / "app_updater.py").read_text(encoding="utf-8")
SHIM = (ROOT / "gui" / "resilient_app_updater.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "app" / "velopack_runtime.py").read_text(encoding="utf-8")
RUNTIME_HOOK = (ROOT / "packaging" / "velopack_runtime_hook.py").read_text(encoding="utf-8")
BROWSER_MANAGER = (ROOT / "gui" / "browser_session_manager.py").read_text(encoding="utf-8")
UPDATE_RUNTIME = (ROOT / "gui" / "update_runtime.py").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
E2E = (ROOT / "scripts" / "test_velopack_update_e2e.ps1").read_text(encoding="utf-8")
PUBLISH = (ROOT / ".github" / "workflows" / "publish-update.yml").read_text(encoding="utf-8")
TEST_PUBLISH = (ROOT / ".github" / "workflows" / "publish-test-build.yml").read_text(encoding="utf-8")
PORTAL_DOWNLOAD = (ROOT / "supabase" / "functions" / "portal-download" / "index.ts").read_text(encoding="utf-8")
PORTAL_RELEASE = (ROOT / "supabase" / "functions" / "portal-release" / "index.ts").read_text(encoding="utf-8")


def test_runtime_uses_official_velopack_manager_and_github_source() -> None:
    assert "import velopack" in RUNTIME
    assert "velopack.GithubSource" in RUNTIME
    assert "velopack.UpdateManager" in RUNTIME
    assert "get_current_version()" in RUNTIME
    assert "Update.exe" in RUNTIME
    assert 'current.name.casefold() != "current"' in RUNTIME


def test_velopack_app_runs_before_normal_pyinstaller_entrypoint() -> None:
    assert "velopack.App().run()" in RUNTIME_HOOK
    assert "wait_exit_then_apply_updates" in RUNTIME_HOOK
    assert "ECOMMERCE_AGENT_UPDATE_E2E_MARKER" in RUNTIME_HOOK


def test_application_update_flow_delegates_transport_install_and_restart_to_velopack() -> None:
    assert "create_update_manager()" in UPDATER
    assert "manager.check_for_updates()" in UPDATER
    assert "manager.download_updates(info" in UPDATER
    assert "manager.wait_exit_then_apply_updates(" in UPDATER
    assert "installer_sha256" not in UPDATER
    assert "UpdaterJob" not in UPDATER
    assert "prepare_standalone_updater" not in UPDATER
    assert "QNetworkAccessManager" not in UPDATER
    assert "subprocess" not in UPDATER


def test_business_idle_and_browser_quiesce_remain_application_policy() -> None:
    assert 'hasattr(manager, "is_busy")' in UPDATER
    assert "begin_update_quiesce" in UPDATER
    assert "wait_for_update_quiesce" in UPDATER
    assert "close_managed_browser" in UPDATER
    assert "_update_quiesced" in BROWSER_MANAGER
    assert "_poll_timer.stop()" in BROWSER_MANAGER
    assert "resume_after_update_failure" in BROWSER_MANAGER


def test_legacy_update_runtime_only_shutdowns_owned_workers() -> None:
    assert "shutdown_owned_qprocesses" in UPDATE_RUNTIME
    assert "prepare_standalone_updater" not in UPDATE_RUNTIME
    assert "updater.exe" not in UPDATE_RUNTIME
    assert "last-result.json" not in UPDATE_RUNTIME


def test_resilient_module_is_only_a_compatibility_import() -> None:
    assert "from gui.app_updater import ApplicationUpdater, install_application_updater" in SHIM
    assert "class ApplicationUpdater" not in SHIM


def test_release_build_and_publish_are_native_velopack() -> None:
    assert "test_velopack_update_e2e.ps1" in BUILD
    assert "Updater.spec" not in BUILD
    assert "installer.iss" not in BUILD
    assert "dotnet tool run vpk -- upload github" in PUBLISH
    assert "artifacts\\velopack" in PUBLISH
    assert "releases.$env:VELOPACK_CHANNEL.json" in PUBLISH
    assert "update.json" not in PUBLISH
    assert "Inno" not in PUBLISH
    assert "dotnet tool run vpk -- upload github" in TEST_PUBLISH


def test_real_e2e_is_old_velopack_to_new_velopack_and_real_qt_gui() -> None:
    assert '$OldVersion = "0.0.1"' in E2E
    assert "$OldAppDir" in E2E
    assert "Set-Content -Path $OldEmbeddedVersion -Value $OldVersion" in E2E
    assert "Get-SingleVelopackArtifact" in E2E
    assert "Resolve-E2EFullPackage" in E2E
    assert '-Filter "$PackId*-Setup.exe"' in E2E
    assert 'Join-Path $FeedDir "$PackId-Setup.exe"' not in E2E
    assert '"$PackId-$Version-full.nupkg"' not in E2E
    assert "--silent" in E2E
    assert "--installto" in E2E
    assert "--velopack-e2e-source" in E2E
    assert "--velopack-e2e-target" in E2E
    assert "real-gui-relaunch.json" in E2E
    assert 'current\\EcommerceAgent.exe' in E2E
    assert "Velopack E2E passed" in E2E


def test_stable_publish_derives_all_native_update_assets_from_feed_or_output() -> None:
    assert 'Get-ChildItem $dir -File -Filter "Smirel.ListingStudio*-Setup.exe"' in PUBLISH
    assert 'Get-ChildItem $dir -File -Filter "Smirel.ListingStudio*-Portable.zip"' in PUBLISH
    assert '$targetFull = [string]$target[0].FileName' in PUBLISH
    assert '"target_full=$targetFull"' in PUBLISH
    assert "$env:target_full" in PUBLISH
    assert '"Smirel.ListingStudio-$env:UPDATE_VERSION-full.nupkg"' not in PUBLISH
    assert '"Smirel.ListingStudio-Setup.exe"' not in PUBLISH
    assert '"Smirel.ListingStudio-Portable.zip"' not in PUBLISH


def test_release_publication_is_transactional_and_checks_portal_installer_digest() -> None:
    assert "Stage Velopack Stable release as draft" in PUBLISH
    assert "--publish false" in PUBLISH
    assert "Verify complete draft before publication" in PUBLISH
    assert "friendly_setup_sha" in PUBLISH
    assert "friendly_setup_size" in PUBLISH
    assert '"sha256:$env:friendly_setup_sha"' in PUBLISH
    assert "gh release edit $tag --draft=false --latest" in PUBLISH
    assert "Cleanup failed Stable draft" in PUBLISH
    assert "gh release delete $tag --yes --cleanup-tag" in PUBLISH
    assert "Stage prerelease Velopack assets as draft" in TEST_PUBLISH
    assert "$env:target_full" in TEST_PUBLISH
    assert "gh release edit $tag --draft=false --prerelease" in TEST_PUBLISH
    assert "Cleanup failed prerelease draft" in TEST_PUBLISH
    assert "Prune old test prereleases" in TEST_PUBLISH
    assert "Select-Object -Skip 3" in TEST_PUBLISH


def test_portal_download_no_longer_depends_on_legacy_update_manifest() -> None:
    assert 'const MANIFEST_ASSET = "update.json"' not in PORTAL_DOWNLOAD
    assert "SHA256_DIGEST_RE" in PORTAL_DOWNLOAD
    assert "installerAsset?.digest" in PORTAL_DOWNLOAD
    assert 'const installerName = `EcommerceAgent-Setup-${requestedVersion}.exe`' in PORTAL_DOWNLOAD
    assert 'source: "github_release_stable"' in PORTAL_DOWNLOAD


def test_public_release_metadata_accepts_velopack_release_and_only_uses_legacy_manifest_optionally() -> None:
    assert 'const LEGACY_MANIFEST_ASSET = "update.json"' in PORTAL_RELEASE
    assert "legacyManifestAsset?.browser_download_url" in PORTAL_RELEASE
    assert "invalid_stable_tag" in PORTAL_RELEASE
    assert "installerAsset?.digest" in PORTAL_RELEASE
    assert 'const installerName = `EcommerceAgent-Setup-${version}.exe`' in PORTAL_RELEASE
    assert "stable_manifest_missing" not in PORTAL_RELEASE
