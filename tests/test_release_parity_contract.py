from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_WORKFLOW = (ROOT / ".github" / "workflows" / "publish-test-build.yml").read_text(encoding="utf-8")
STABLE_WORKFLOW = (ROOT / ".github" / "workflows" / "publish-update.yml").read_text(encoding="utf-8")
LOCK = (ROOT / "requirements-release.lock").read_text(encoding="utf-8")
INSTALL = (ROOT / "scripts" / "install_release_environment.ps1").read_text(encoding="utf-8")
DOTNET_CONTRACT = json.loads((ROOT / "global.json").read_text(encoding="utf-8"))


def _require_in_both(fragment: str) -> None:
    assert fragment in TEST_WORKFLOW
    assert fragment in STABLE_WORKFLOW


def test_test_and_stable_share_one_release_environment_contract() -> None:
    for fragment in (
        'runs-on: windows-2025',
        'python-version: "3.11.9"',
        'dotnet-version: "8.0.424"',
        'cache-dependency-path: requirements-release.lock',
        '.\\scripts\\install_release_environment.ps1',
        '.\\scripts\\build_windows.ps1',
        'tests/test_release_parity_contract.py',
        'Require dispatcher/source workflow parity',
    ):
        _require_in_both(fragment)

    assert "python -m pip install -r requirements.txt" not in TEST_WORKFLOW
    assert "python -m pip install -r requirements.txt" not in STABLE_WORKFLOW
    assert "pip install --upgrade pip" not in TEST_WORKFLOW
    assert "pip install --upgrade pip" not in STABLE_WORKFLOW


def test_dotnet_sdk_is_repository_pinned_and_install_reads_that_single_contract() -> None:
    sdk = DOTNET_CONTRACT["sdk"]
    assert sdk["version"] == "8.0.424"
    assert sdk["rollForward"] == "disable"
    assert sdk["allowPrerelease"] is False
    assert '$GlobalJson = Join-Path $Root "global.json"' in INSTALL
    assert '$ExpectedDotNet = [string]$DotNetContract.sdk.version' in INSTALL
    assert '$DotNetRollForward -ne "disable"' in INSTALL
    assert 'global_json_sha256 = $GlobalJsonSha' in INSTALL


def test_release_lock_is_exact_and_covers_packaged_top_level_dependencies() -> None:
    requirements = [
        line.strip()
        for line in LOCK.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert requirements
    assert all("==" in line for line in requirements)
    assert all(not any(operator in line for operator in (">=", "<=", "~=", "!=", "<", ">")) for line in requirements)
    for expected in (
        "playwright==1.62.0",
        "openai==2.54.0",
        "Pillow==12.3.0",
        "PySide6==6.11.2",
        "PyInstaller==6.22.2",
        "velopack==1.2.0",
    ):
        assert expected in requirements


def test_release_environment_isolated_and_records_provenance() -> None:
    assert 'python -m venv $VenvRoot' in INSTALL
    assert '"pip==$ExpectedPip"' in INSTALL
    assert '-r $LockFile' in INSTALL
    assert '-m pip check' in INSTALL
    assert '$env:GITHUB_PATH' in INSTALL
    assert 'source_sha = $SourceSha' in INSTALL
    assert 'release_lock_sha256 = $LockSha' in INSTALL
    assert 'pyinstaller_spec_sha256 = $SpecSha' in INSTALL
    assert 'build_windows_sha256 = $BuildScriptSha' in INSTALL
    assert 'runner_image_version = [string]$env:ImageVersion' in INSTALL


def test_stable_can_optionally_validate_the_same_source_and_release_contract() -> None:
    assert "validated_test_tag:" in STABLE_WORKFLOW
    assert "Verify optional Test candidate" in STABLE_WORKFLOW
    assert 'test-$version-$short-*' in STABLE_WORKFLOW
    assert 'release-environment.json' in STABLE_WORKFLOW
    assert 'release_lock_sha256' in STABLE_WORKFLOW
    assert 'pyinstaller_spec_sha256' in STABLE_WORKFLOW
    assert 'build_windows_sha256' in STABLE_WORKFLOW


def test_test_workflow_matches_current_velopack_outputs_not_removed_msi_path() -> None:
    assert "EcommerceAgent-Setup-${{ steps.build.outputs.version }}.exe" in TEST_WORKFLOW
    assert "EcommerceAgent-${{ steps.build.outputs.version }}-portable.zip" in TEST_WORKFLOW
    assert ".msi" not in TEST_WORKFLOW.casefold()
    assert "test_velopack_msi.ps1" not in TEST_WORKFLOW


def test_stable_signing_is_optional_but_configuration_remains_validated() -> None:
    assert "Validate optional Stable code signing" in STABLE_WORKFLOW
    assert "Stable publication is unsigned" in STABLE_WORKFLOW
    assert "Stable publication requires code signing" not in STABLE_WORKFLOW
    assert "Configure exactly one Stable signing mode, not both." in STABLE_WORKFLOW
    assert "VPK_AZURE_TRUSTED_SIGN_FILE" in STABLE_WORKFLOW
    assert "VPK_SIGN_PARAMS" in STABLE_WORKFLOW
    assert "VPK_AZURE_TRUSTED_SIGN_FILE" not in TEST_WORKFLOW
    assert "VPK_SIGN_PARAMS" not in TEST_WORKFLOW
