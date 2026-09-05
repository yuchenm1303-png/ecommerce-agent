from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_large_windows_binaries_are_never_duplicated_in_actions_artifact_storage() -> None:
    stable = _workflow("publish-update.yml")
    test = _workflow("publish-test-build.yml")
    package_ci = _workflow("windows-package.yml")

    for source in (stable, test, package_ci):
        assert "actions/upload-artifact@" not in source

    # Stable/Test distribution remains owned by verified GitHub Releases.
    assert "gh release upload" in stable
    assert "Verify published Velopack contract" in stable
    assert "gh release upload" in test
    assert "Verify published prerelease contract" in test
