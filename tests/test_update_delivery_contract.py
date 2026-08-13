from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCESS = (ROOT / "gui" / "app_access.py").read_text(encoding="utf-8")
UPDATER = (ROOT / "gui" / "app_updater.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "EcommerceAgent.spec").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
INSTALLER = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
PUBLISH = (ROOT / ".github" / "workflows" / "publish-update.yml").read_text(encoding="utf-8")
WINDOWS_PACKAGE = (ROOT / ".github" / "workflows" / "windows-package.yml").read_text(encoding="utf-8")


def test_access_and_updater_sources_compile_without_importing_qt() -> None:
    compile(ACCESS, str(ROOT / "gui" / "app_access.py"), "exec")
    compile(UPDATER, str(ROOT / "gui" / "app_updater.py"), "exec")


def test_packaged_app_requires_account_and_device_access_but_source_runs_do_not() -> None:
    assert 'if not bool(getattr(sys, "frozen", False))' in ACCESS
    assert 'return ApplicationAccessSession.development()' in ACCESS
    assert '_LICENSE_URL = f"{_SUPABASE_URL}/functions/v1/portal-license"' in ACCESS
    assert 'action="activate"' in ACCESS
    assert 'action="validate"' in ACCESS
    assert 'CryptProtectData' in ACCESS
    assert 'CryptUnprotectData' in ACCESS
    assert 'SUPABASE_SERVICE_ROLE' not in ACCESS
    assert 'ensure_application_access(app)' in RUN
    assert 'install_application_access(window, access_session)' in RUN


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


def test_updater_supports_legacy_github_and_authorized_private_delivery() -> None:
    assert '_PORTAL_URL = "https://smirel.com/download/"' in UPDATER
    assert 'manifest.get("delivery") or "github"' in UPDATER
    assert 'delivery == "portal"' in UPDATER
    assert '_download_portal_update' in UPDATER
    assert 'access_controller' in UPDATER
    assert 'Authorization' in UPDATER
    assert 'apikey' in UPDATER
    assert '_PRIVATE_DOWNLOAD_HOSTS' in UPDATER
    assert 'QDesktopServices.openUrl' in UPDATER
    assert 'url.host().lower() not in _PORTAL_HOSTS' in UPDATER
    assert 'url.host().lower() != "github.com"' in UPDATER
    assert '_expected_github_installer_path' in UPDATER
    assert 'install_application_updater(window, access_controller=access_controller)' in RUN


def test_update_has_continuous_visible_download_verify_install_and_relaunch_flow() -> None:
    assert 'QProgressDialog' in UPDATER
    assert '正在验证更新权限' in UPDATER
    assert '正在下载更新' in UPDATER
    assert '正在校验更新包完整性' in UPDATER
    assert '正在启动安装程序' in UPDATER
    assert '更新完成后会自动重新打开' in UPDATER
    assert '_write_update_marker' in UPDATER
    assert '_consume_completed_update_marker' in UPDATER
    assert '更新已完成' in UPDATER
    assert "update-complete.json" in UPDATER
    assert "update-complete.json" in INSTALLER


def test_download_is_https_stream_hash_verified_and_runs_visible_installer_progress() -> None:
    assert 'url.scheme().lower() != "https"' in UPDATER
    assert 'hashlib.sha256()' in UPDATER
    assert 'stream.read(1024 * 1024)' in UPDATER
    assert 'installer_sha256' in UPDATER
    assert 'QProcess.startDetached' in UPDATER
    for flag in (
        '"/SILENT"',
        '"/SUPPRESSMSGBOXES"',
        '"/NORESTART"',
        '"/CLOSEAPPLICATIONS"',
        '"/NORESTARTAPPLICATIONS"',
    ):
        assert flag in UPDATER
    assert "RestartApplications=yes" in INSTALLER
    assert "FileExists(ExpandConstant('{localappdata}\\ListingStudio\\update-complete.json'))" in INSTALLER


def test_frozen_package_embeds_the_exact_build_version() -> None:
    assert 'ECOMMERCE_AGENT_BUILD_VERSION' in SPEC
    assert 'BUILD_METADATA / "VERSION"' in SPEC
    assert '(str(BUILD_METADATA / "VERSION"), "packaging")' in SPEC
    assert '$env:ECOMMERCE_AGENT_BUILD_VERSION = $Version' in BUILD
    assert 'installed_application_version()' in UPDATER


def test_formal_gui_checks_after_startup_without_affecting_source_runs() -> None:
    assert 'from gui.app_updater import install_application_updater' in RUN
    assert 'install_application_updater(window, access_controller=access_controller)' in RUN
    assert RUN.index('entrance_stability.start()') < RUN.index('install_application_updater(window, access_controller=access_controller)')
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
