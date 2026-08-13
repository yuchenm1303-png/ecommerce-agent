from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATER = (ROOT / "gui" / "app_updater.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "EcommerceAgent.spec").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
INSTALLER = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
PUBLISH = (ROOT / ".github" / "workflows" / "publish-update.yml").read_text(encoding="utf-8")
WINDOWS_PACKAGE = (ROOT / ".github" / "workflows" / "windows-package.yml").read_text(encoding="utf-8")


def test_updater_source_compiles_without_importing_qt() -> None:
    compile(UPDATER, str(ROOT / "gui" / "app_updater.py"), "exec")


def test_client_reads_only_manually_published_stable_release() -> None:
    assert 'releases/latest' in UPDATER
    assert '_MANIFEST_ASSET = "update.json"' in UPDATER
    assert 'manifest.get("channel") != "stable"' in UPDATER
    assert 'bool(getattr(sys, "frozen", False))' in UPDATER
    assert 'ECOMMERCE_AGENT_DISABLE_UPDATE_CHECK' in UPDATER
    assert 'actions/artifacts' not in UPDATER
    assert 'workflow_runs' not in UPDATER


def test_update_policy_supports_optional_required_and_minimum_supported_versions() -> None:
    assert 'manifest.get("required", False)' in UPDATER
    assert 'min_supported_version' in UPDATER
    assert 'current_key < minimum_key' in UPDATER
    assert '"立即更新"' in UPDATER
    assert '"稍后"' in UPDATER
    assert '"退出程序"' in UPDATER


def test_download_is_https_hash_verified_and_runs_existing_installer() -> None:
    assert 'url.scheme().lower() != "https"' in UPDATER
    assert 'url.host().lower() != "github.com"' in UPDATER
    assert 'hashlib.sha256(path.read_bytes()).hexdigest()' in UPDATER
    assert 'installer_sha256' in UPDATER
    assert 'QProcess.startDetached' in UPDATER
    for flag in (
        '"/VERYSILENT"',
        '"/SUPPRESSMSGBOXES"',
        '"/NORESTART"',
        '"/CLOSEAPPLICATIONS"',
        '"/RESTARTAPPLICATIONS"',
    ):
        assert flag in UPDATER
    assert "RestartApplications=yes" in INSTALLER


def test_frozen_package_embeds_the_exact_build_version() -> None:
    assert 'ECOMMERCE_AGENT_BUILD_VERSION' in SPEC
    assert 'BUILD_METADATA / "VERSION"' in SPEC
    assert '(str(BUILD_METADATA / "VERSION"), "packaging")' in SPEC
    assert '$env:ECOMMERCE_AGENT_BUILD_VERSION = $Version' in BUILD
    assert 'installed_application_version()' in UPDATER


def test_formal_gui_checks_after_startup_without_affecting_source_runs() -> None:
    assert 'from gui.app_updater import install_application_updater' in RUN
    assert 'install_application_updater(window)' in RUN
    assert RUN.index('entrance_stability.start()') < RUN.index('install_application_updater(window)')
    assert '_CHECK_DELAY_MS = 1800' in UPDATER


def test_publish_update_is_manual_only_and_creates_stable_release_manifest() -> None:
    assert 'workflow_dispatch:' in PUBLISH
    assert '\n  push:' not in PUBLISH
    assert 'update_type:' in PUBLISH
    assert 'min_supported_version:' in PUBLISH
    assert 'permissions:\n  contents: write' in PUBLISH
    assert 'build_windows.ps1 -Version $env:UPDATE_VERSION' in PUBLISH
    assert 'installer_sha256' in PUBLISH
    assert 'channel = "stable"' in PUBLISH
    assert 'gh release create' in PUBLISH
    assert 'artifacts\\update.json' in PUBLISH


def test_ordinary_windows_package_never_publishes_user_updates() -> None:
    assert 'actions/upload-artifact@v4' in WINDOWS_PACKAGE
    assert 'gh release create' not in WINDOWS_PACKAGE
    assert 'releases/latest' not in WINDOWS_PACKAGE
