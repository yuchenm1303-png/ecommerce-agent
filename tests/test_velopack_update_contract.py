from __future__ import annotations

import py_compile
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
PORTAL_RELEASE_WARM = (
    ROOT / "supabase" / "functions" / "portal-release-warm" / "index.ts"
).read_text(encoding="utf-8")
UPDATE_MIRROR_MIGRATION = (
    ROOT / "supabase" / "migrations" / "20260902052000_resilient_update_mirror.sql"
).read_text(encoding="utf-8")
UPDATE_MIRROR_CHUNK_LIMIT_MIGRATION = (
    ROOT / "supabase" / "migrations" / "20260902054500_chunked_update_mirror_limit.sql"
).read_text(encoding="utf-8")


def test_github_remains_release_authority_while_control_plane_routes_identical_velopack_feeds() -> None:
    assert "import velopack" in RUNTIME
    assert "velopack.GithubSource" in RUNTIME
    assert "velopack.UpdateManager" in RUNTIME
    assert "class StableUpdateRoute" in RUNTIME
    assert "PORTAL_RELEASE_URL" in RUNTIME
    assert 'if GITHUB_REPOSITORY_URL not in sources:' in RUNTIME
    assert "actual != route.advertised_version" in RUNTIME
    assert "override != GITHUB_REPOSITORY_URL" in RUNTIME
    assert "get_current_version()" in RUNTIME
    assert "Update.exe" in RUNTIME
    assert 'current.name.casefold() != "current"' in RUNTIME


def test_velopack_app_runs_before_normal_pyinstaller_entrypoint() -> None:
    assert "velopack.App().run()" in RUNTIME_HOOK
    assert "get_update_pending_restart()" in RUNTIME_HOOK
    assert "apply_updates_and_restart_with_args" in RUNTIME_HOOK
    assert "wait_exit_then_apply_updates" not in RUNTIME_HOOK
    assert "ECOMMERCE_AGENT_UPDATE_E2E_MARKER" in RUNTIME_HOOK


def test_application_update_flow_delegates_package_semantics_install_and_restart_to_velopack() -> None:
    assert "resolve_stable_update_route()" in UPDATER
    assert "check_update_route(route, self.current_version)" in UPDATER
    assert "manager.check_for_updates()" not in UPDATER
    assert "download_update_with_failover(" in UPDATER
    assert "manager.get_update_pending_restart()" in UPDATER
    assert "manager.apply_updates_and_restart(pending)" in UPDATER
    assert "wait_exit_then_apply_updates" not in UPDATER
    assert "QTimer.singleShot(180, QApplication.quit)" not in UPDATER
    assert "installer_sha256" not in UPDATER
    assert "UpdaterJob" not in UPDATER
    assert "prepare_standalone_updater" not in UPDATER
    assert "QNetworkAccessManager" not in UPDATER
    assert "subprocess" not in UPDATER


def test_update_discovery_and_download_are_isolated_from_the_qt_process() -> None:
    assert "_UPDATE_CHECK_TIMEOUT_SECONDS = 12.0" in RUNTIME
    assert "def _run_check_worker() -> int:" in RUNTIME
    assert "def _run_download_worker() -> int:" in RUNTIME
    assert "create_update_manager(source).check_for_updates()" in RUNTIME
    assert "subprocess.Popen(" in RUNTIME
    assert "process.wait(timeout=timeout)" in RUNTIME
    assert "except subprocess.TimeoutExpired" in RUNTIME
    assert "process.kill()" in RUNTIME
    assert "raise UpdateCheckTimeoutError(" in RUNTIME
    assert "_UPDATE_CHECK_RESULT_ENV" in RUNTIME
    assert '_update_info_from_payload(payload.get("info"))' in RUNTIME
    assert "_UPDATE_DOWNLOAD_IDLE_TIMEOUT_SECONDS = 45.0" in RUNTIME
    assert "update download made no progress" in RUNTIME


def test_chunked_update_transport_compiles_and_disables_implicit_windows_proxy(monkeypatch) -> None:
    module_path = ROOT / "app" / "chunked_update_transport.py"
    py_compile.compile(str(module_path), doraise=True)
    from app import chunked_update_transport as transport

    for name in (
        "ALL_PROXY",
        "all_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    handler = transport._configured_proxy_handler()
    assert getattr(handler, "proxies", {}) == {}
    assert transport._RETRY_DELAYS_SECONDS == (1.0, 3.0, 7.0)


def test_business_idle_and_browser_quiesce_remain_application_policy() -> None:
    assert 'hasattr(manager, "is_busy")' in UPDATER
    assert "begin_update_quiesce" in UPDATER
    assert "wait_for_update_quiesce" in UPDATER
    assert "close_managed_browser" in UPDATER
    assert "shutdown_owned_qprocesses" in UPDATER
    assert UPDATER.rindex("shutdown_owned_qprocesses") < UPDATER.rindex("apply_updates_and_restart")
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
    assert '$releaseName = "$env:UPDATE_TITLE v$resolved"' in PUBLISH
    assert '"UPDATE_RELEASE_NAME=$releaseName"' in PUBLISH
    assert '--releaseName "$env:UPDATE_RELEASE_NAME"' in PUBLISH
    assert '--releaseName "$env:UPDATE_TITLE"' not in PUBLISH
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
    assert 'ArgumentList @("--silent", "uninstall")' in E2E
    assert "Velopack E2E uninstall left installation root behind" in E2E
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
    assert "Stage prerelease Velopack assets as draft" in TEST_PUBLISH
    assert "$env:target_full" in TEST_PUBLISH
    assert "gh release edit $tag --draft=false --prerelease" in TEST_PUBLISH
    assert "Cleanup failed prerelease draft" in TEST_PUBLISH
    assert "Prune old test prereleases" in TEST_PUBLISH
    assert "Select-Object -Skip 3" in TEST_PUBLISH


def test_portal_download_serves_authorized_mirrored_stable_chunks_without_github_resolution() -> None:
    assert 'const MANIFEST_ASSET = "update.json"' not in PORTAL_DOWNLOAD
    assert 'const DOWNLOAD_BUCKET = "listing-studio-downloads"' in PORTAL_DOWNLOAD
    assert "SHA256_HEX_RE" in PORTAL_DOWNLOAD
    assert "resolveLatestStable()" in PORTAL_DOWNLOAD
    assert "loadInstallerManifest(version)" in PORTAL_DOWNLOAD
    assert ".createSignedUrls(paths, SIGNED_CHUNK_TTL_SECONDS)" in PORTAL_DOWNLOAD
    assert 'error: "version_not_mirrored"' in PORTAL_DOWNLOAD
    assert 'throw new Error("latest_installer_mirror_mismatch")' in PORTAL_DOWNLOAD
    assert 'action === "download_latest"' in PORTAL_DOWNLOAD
    assert 'action === "download_version"' in PORTAL_DOWNLOAD
    assert "https://api.github.com/" not in PORTAL_DOWNLOAD
    assert "browser_download_url" not in PORTAL_DOWNLOAD
    assert "installerAsset?.digest" not in PORTAL_DOWNLOAD


def test_public_release_metadata_is_mirror_only_and_never_falls_back_to_private_github() -> None:
    assert 'const UPDATE_BUCKET = "listing-studio-updates"' in PORTAL_RELEASE
    assert 'const STABLE_CACHE_PATH = "stable/latest.json"' in PORTAL_RELEASE
    assert "SHA256_HEX_RE" in PORTAL_RELEASE
    assert "loadStableCache(admin)" in PORTAL_RELEASE
    assert "releaseMirrorReady(admin, stable)" in PORTAL_RELEASE
    assert "chunkManifestReady(" in PORTAL_RELEASE
    assert "objectReady(" in PORTAL_RELEASE
    assert "const MIRROR_CHUNK_BYTES = 32 * 1024 * 1024" in PORTAL_RELEASE
    assert "updateSources: [baseUrl]" in PORTAL_RELEASE
    assert "mirrorReady: true" in PORTAL_RELEASE
    assert "packageMirrorReady: true" in PORTAL_RELEASE
    assert "stable_mirror_incomplete" in PORTAL_RELEASE
    assert "GITHUB_UPDATE_SOURCE" not in PORTAL_RELEASE
    assert "LEGACY_MANIFEST_ASSET" not in PORTAL_RELEASE
    assert "browser_download_url" not in PORTAL_RELEASE
    assert "https://api.github.com/" not in PORTAL_RELEASE
    assert '"Range"' not in PORTAL_RELEASE


def test_private_release_warmer_owns_github_validation_and_transactional_mirroring() -> None:
    assert 'const REPOSITORY = "yuchenm1303-png/ecommerce-agent"' in PORTAL_RELEASE_WARM
    assert 'const UPDATE_BUCKET = "listing-studio-updates"' in PORTAL_RELEASE_WARM
    assert 'const DOWNLOAD_BUCKET = "listing-studio-downloads"' in PORTAL_RELEASE_WARM
    assert "SHA256_DIGEST_RE" in PORTAL_RELEASE_WARM
    assert "githubHeaders(token" in PORTAL_RELEASE_WARM
    assert "getRelease(token, tag)" in PORTAL_RELEASE_WARM
    assert "parseAsset(release, version" in PORTAL_RELEASE_WARM
    assert "https://api.github.com/repos/${REPOSITORY}/releases/assets/${asset.id}" in PORTAL_RELEASE_WARM
    assert 'Range: `bytes=${part.offset}-${end}`' in PORTAL_RELEASE_WARM
    assert "ensureChunkedAsset(" in PORTAL_RELEASE_WARM
    assert "const ready = fullReady && installerReady" in PORTAL_RELEASE_WARM
    assert "if (ready)" in PORTAL_RELEASE_WARM
    assert "buildStableCache(release, version, installer, feed, packages)" in PORTAL_RELEASE_WARM
    assert "persistJson(admin, UPDATE_BUCKET, STABLE_CACHE_PATH" in PORTAL_RELEASE_WARM
    assert "github_token_cannot_read_release" in PORTAL_RELEASE_WARM


def test_update_mirror_bucket_is_public_and_effectively_chunk_limited() -> None:
    assert "'listing-studio-updates'" in UPDATE_MIRROR_MIGRATION
    assert "true" in UPDATE_MIRROR_MIGRATION
    assert "application/json" in UPDATE_MIRROR_MIGRATION
    assert "application/octet-stream" in UPDATE_MIRROR_MIGRATION
    assert "file_size_limit = 52428800" in UPDATE_MIRROR_CHUNK_LIMIT_MIGRATION
    assert "public = true" in UPDATE_MIRROR_CHUNK_LIMIT_MIGRATION
