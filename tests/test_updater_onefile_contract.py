from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATER_SPEC = (ROOT / "packaging" / "Updater.spec").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
LEGACY_ENTRY = (ROOT / "scripts" / "updater_main.py").read_text(encoding="utf-8")
ENTRY = (ROOT / "scripts" / "updater_main_v2.py").read_text(encoding="utf-8")


def test_standalone_updater_embeds_python_runtime_in_single_executable() -> None:
    assert "updater_a.binaries" in UPDATER_SPEC
    assert "updater_a.datas" in UPDATER_SPEC
    assert "exclude_binaries=False" in UPDATER_SPEC
    assert "COLLECT(" not in UPDATER_SPEC
    assert 'name="updater"' in UPDATER_SPEC
    assert "console=False" in UPDATER_SPEC
    assert "updater_main_v2.py" in UPDATER_SPEC


def test_self_check_imports_the_embedded_updater_core_through_v2_entry() -> None:
    assert "from app.updater_core import JOB_VERSION, UpdaterJob, run_job" in LEGACY_ENTRY
    assert "JOB_VERSION < 2" in LEGACY_ENTRY
    assert "callable(run_job)" in LEGACY_ENTRY
    assert "legacy._self_check()" in ENTRY
    assert "NativeUpdatePanel" in ENTRY


def test_windows_build_executes_updater_before_packaging_it() -> None:
    build_updater = BUILD.index('packaging\\Updater.spec')
    self_check = BUILD.index("& $UpdaterExe --self-check")
    copy_updater = BUILD.index(
        'Copy-Item $UpdaterExe (Join-Path $AppDir "updater\\updater.exe") -Force'
    )
    assert build_updater < self_check < copy_updater
    assert "Standalone updater self-check failed" in BUILD
