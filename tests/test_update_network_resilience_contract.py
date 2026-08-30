from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "app" / "velopack_runtime.py").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")


def test_client_retries_transient_velopack_disconnects_without_owning_update_semantics() -> None:
    assert '"peer disconnected"' in RUNTIME
    assert '_DOWNLOAD_RETRY_DELAYS_SECONDS = (2.0, 5.0)' in RUNTIME
    assert 'return self._manager.download_updates(info, progress)' in RUNTIME
    assert 'update network transport failed after {attempt} attempts' in RUNTIME
    assert 'ChecksumFailedException' not in RUNTIME
    assert 'apply_updates_and_restart' not in RUNTIME


def test_stable_build_hydrates_previous_release_and_requires_a_delta() -> None:
    assert 'download github' in BUILD
    assert '--repoUrl "https://github.com/yuchenm1303-png/ecommerce-agent"' in BUILD
    assert '--channel $Channel' in BUILD
    assert '--outputDir $VelopackDir' in BUILD
    assert '"--delta", "BestSize"' in BUILD
    assert 'Resolve-VelopackDeltaPackage' in BUILD
    assert '[string]$_.Type -eq "Delta"' in BUILD
    assert 'Stable publication requires the previous public Velopack release' in BUILD
