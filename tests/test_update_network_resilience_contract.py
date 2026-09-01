from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "app" / "velopack_runtime.py").read_text(encoding="utf-8")
UPDATER = (ROOT / "gui" / "app_updater.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")


def test_client_retries_transient_velopack_disconnects_without_owning_update_semantics() -> None:
    assert '"peer disconnected"' in RUNTIME
    assert '_DOWNLOAD_RETRY_DELAYS_SECONDS = (2.0, 5.0)' in RUNTIME
    assert 'return self._manager.download_updates(info, progress)' in RUNTIME
    assert 'update network transport failed after {attempt} attempts' in RUNTIME
    assert 'ChecksumFailedException' not in RUNTIME
    assert 'apply_updates_and_restart' not in RUNTIME


def test_update_discovery_runs_in_a_killable_process_with_a_hard_deadline() -> None:
    assert '_UPDATE_CHECK_TIMEOUT_SECONDS = 12.0' in RUNTIME
    assert 'subprocess.Popen(' in RUNTIME
    assert 'stdout, stderr = process.communicate(timeout=timeout)' in RUNTIME
    assert 'except subprocess.TimeoutExpired as exc:' in RUNTIME
    assert 'process.kill()' in RUNTIME
    assert 'raise UpdateCheckTimeoutError(' in RUNTIME
    assert 'bounded_check_for_updates' in UPDATER
    assert 'info = bounded_check_for_updates(source_url)' in UPDATER
    assert 'manager.check_for_updates()' not in UPDATER
    assert 'if "--internal-velopack-check" in sys.argv[1:]:' in RUN
    assert 'return run_update_check_worker()' in RUN


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
