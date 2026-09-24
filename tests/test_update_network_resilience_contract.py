from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "app" / "velopack_runtime.py").read_text(encoding="utf-8")
UPDATER = (ROOT / "gui" / "app_updater.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "EcommerceAgent.spec").read_text(encoding="utf-8")


def test_client_retries_transient_velopack_disconnects_without_owning_update_semantics() -> None:
    assert '"peer disconnected"' in RUNTIME
    assert '_DOWNLOAD_RETRY_DELAYS_SECONDS = (2.0, 5.0)' in RUNTIME
    assert 'return self._manager.download_updates(info, progress)' in RUNTIME
    assert 'update network transport failed after {attempt} attempts' in RUNTIME
    assert 'ChecksumFailedException' not in RUNTIME
    assert 'apply_updates_and_restart' not in RUNTIME


def test_update_discovery_uses_control_plane_cdn_and_github_fallback() -> None:
    assert 'PORTAL_RELEASE_URL = "https://nfzkphjbelyltrzgkdwt.supabase.co/functions/v1/portal-release"' in RUNTIME
    assert 'class StableUpdateRoute' in RUNTIME
    assert 'payload.get("updateSources")' in RUNTIME
    assert 'if GITHUB_REPOSITORY_URL not in sources:' in RUNTIME
    assert 'resolve_stable_update_route()' in UPDATER
    assert 'check_update_route(route, self.current_version)' in UPDATER


def test_update_workers_inherit_system_proxy_and_also_keep_a_direct_fallback() -> None:
    assert 'urllib.request.getproxies()' in RUNTIME
    assert 'env.setdefault("HTTPS_PROXY", https_proxy)' in RUNTIME
    assert 'env.setdefault("HTTP_PROXY", http_proxy)' in RUNTIME
    assert 'for name in _PROXY_ENV_NAMES:' in RUNTIME
    assert 'modes = (route.prefer_system_proxy, not route.prefer_system_proxy)' in RUNTIME


def test_update_discovery_runs_in_a_killable_process_with_a_hard_deadline() -> None:
    assert '_UPDATE_CHECK_TIMEOUT_SECONDS = 12.0' in RUNTIME
    assert 'subprocess.Popen(' in RUNTIME
    assert 'process.wait(timeout=timeout)' in RUNTIME
    assert 'except subprocess.TimeoutExpired as exc:' in RUNTIME
    assert 'process.kill()' in RUNTIME
    assert 'raise UpdateCheckTimeoutError(' in RUNTIME
    assert 'bounded_check_for_updates' in RUNTIME
    assert 'if "--internal-velopack-check" in sys.argv[1:]:' in RUN
    assert 'return run_update_check_worker()' in RUN


def test_frozen_update_worker_uses_file_ipc_instead_of_console_streams() -> None:
    assert 'console=False' in SPEC
    assert '_UPDATE_CHECK_RESULT_ENV = "ECOMMERCE_AGENT_UPDATE_CHECK_RESULT_PATH"' in RUNTIME
    assert 'tempfile.TemporaryDirectory(prefix="listing-studio-update-check-")' in RUNTIME
    assert 'tempfile.TemporaryDirectory(prefix="listing-studio-update-download-")' in RUNTIME
    assert 'os.replace(temp, path)' in RUNTIME
    assert 'stdout=subprocess.DEVNULL' in RUNTIME
    assert 'stderr=subprocess.DEVNULL' in RUNTIME


def test_update_download_is_isolated_has_idle_timeout_and_fails_over_sources() -> None:
    assert '_UPDATE_DOWNLOAD_CHECK_TIMEOUT_SECONDS = 12.0' in RUNTIME
    assert '_UPDATE_DOWNLOAD_IDLE_TIMEOUT_SECONDS = 45.0' in RUNTIME
    assert 'mode == "download"' in RUNTIME
    assert 'download_update_with_failover' in RUNTIME
    assert 'update download made no progress' in RUNTIME
    assert 'process.kill()' in RUNTIME
    assert 'download_update_with_failover(' in UPDATER
    assert 'preferred_source=source_url' in UPDATER


def test_manual_update_check_adopts_an_inflight_startup_check_instead_of_silently_returning() -> None:
    assert 'if self._checking:' in UPDATER
    assert 'if manual:' in UPDATER
    assert 'self._manual_check = True' in UPDATER
    assert 'self._set_manual_check_busy(True)' in UPDATER
    assert 'token = self._check_token' in UPDATER


def test_stable_build_hydrates_previous_release_and_requires_a_delta() -> None:
    assert 'download github' in BUILD
    assert '--repoUrl "https://github.com/yuchenm1303-png/ecommerce-agent"' in BUILD
    assert '--channel $Channel' in BUILD
    assert '--outputDir $VelopackDir' in BUILD
    assert '"--delta", "BestSize"' in BUILD
    assert 'Resolve-VelopackDeltaPackage' in BUILD
    assert '[string]$_.Type -eq "Delta"' in BUILD
    assert 'Stable publication requires the previous public Velopack release' in BUILD
