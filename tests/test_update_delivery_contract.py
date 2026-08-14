from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATER_PATH = ROOT / "gui" / "app_updater.py"
UPDATER = UPDATER_PATH.read_text(encoding="utf-8")
ACCESS = (ROOT / "gui" / "app_access.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
PUBLISH = (ROOT / ".github" / "workflows" / "publish-update.yml").read_text(encoding="utf-8")
WINDOWS_PACKAGE = (ROOT / ".github" / "workflows" / "windows-package.yml").read_text(encoding="utf-8")


def test_updater_and_access_sources_compile() -> None:
    compile(UPDATER, str(UPDATER_PATH), "exec")
    compile(ACCESS, str(ROOT / "gui" / "app_access.py"), "exec")


def test_formal_app_uses_manual_stable_release_channel() -> None:
    assert "releases/latest" in UPDATER
    assert '_MANIFEST_ASSET = "update.json"' in UPDATER
    assert 'payload.get("channel") != "stable"' in UPDATER
    assert "workflow_dispatch:" in PUBLISH
    assert "gh release create" in PUBLISH
    assert "installer_sha256" in PUBLISH
    assert "channel = \"stable\"" in PUBLISH
    assert "gh release create" not in WINDOWS_PACKAGE


def test_update_policy_and_authorized_delivery_are_preserved() -> None:
    assert 'manifest.get("required", False)' in UPDATER
    assert "min_supported_version" in UPDATER
    assert "current_key < minimum_key" in UPDATER
    assert 'payload.get("delivery") or "portal"' in UPDATER
    assert 'delivery == "portal"' in UPDATER
    assert 'delivery == "github"' in UPDATER
    assert "_download_portal_update" in UPDATER
    assert "_expected_github_installer_path" in UPDATER
    assert "access_controller=access_controller" in RUN


def test_update_user_experience_is_continuous_and_visible() -> None:
    for text in (
        "发现新版本",
        "立即更新",
        "稍后",
        "正在验证更新权限",
        "正在下载更新",
        "正在校验更新包完整性",
        "正在启动安装程序",
        "更新完成后会自动重新打开",
        "更新已完成",
    ):
        assert text in UPDATER
    assert "QProgressDialog" in UPDATER
    assert "downloadProgress.connect" in UPDATER


def test_download_is_stream_hash_verified_before_install() -> None:
    assert "hashlib.sha256()" in UPDATER
    assert "stream.read(1024 * 1024)" in UPDATER
    assert "installer_sha256" in UPDATER
    assert "UpdaterJob" in UPDATER
    assert "_handoff_installer" in UPDATER
    for flag in (
        '"/SILENT"',
        '"/SUPPRESSMSGBOXES"',
        '"/NORESTART"',
        '"/CLOSEAPPLICATIONS"',
        '"/NORESTARTAPPLICATIONS"',
    ):
        assert flag in UPDATER


def test_installer_handoff_prefers_standalone_updater_with_in_app_fallback() -> None:
    assert "_handoff_installer" in UPDATER
    assert "ensure_updater_installed" in UPDATER
    assert "_launch_standalone_updater" in UPDATER
    assert "pending-update.json" in UPDATER
    assert "UpdaterJob" in UPDATER
    assert "app_pid=os.getpid()" in UPDATER
    assert "app_image_name=Path(sys.executable).stem" in UPDATER
    # The in-app PowerShell waiter stays only as the source/dev fallback.
    assert "_launch_installer_waiter" in UPDATER
    assert "arg.replace(chr(39), chr(39) * 2)" in UPDATER


def test_standalone_updater_lives_outside_the_install_directory() -> None:
    assert "_updater_stable_dir" in UPDATER
    assert "_bundled_updater_exe" in UPDATER
    assert "LOCALAPPDATA" in UPDATER
    assert '"updater"' in UPDATER


def test_update_closes_modal_progress_before_handing_off_installer() -> None:
    block = UPDATER.split("def _verify_and_install", 1)[1]
    assert "self._close_progress()" in block
    assert '_handoff_installer(path, arguments, str(manifest["installer_sha256"]))' in block
    assert "QTimer.singleShot(120, QApplication.quit)" in block
    assert "QProcess.startDetached" not in block


def test_silent_update_explicitly_relaunches_and_confirms_new_version() -> None:
    assert "update-complete.json" in UPDATER
    assert "_write_update_marker" in UPDATER
    assert "_consume_completed_update_marker" in UPDATER
    assert "update-complete.json" in INSTALLER
    assert "FileExists" in INSTALLER
    assert 'Filename: "{app}\\{#MyAppExeName}"' in INSTALLER


def test_formal_gui_checks_after_visible_startup() -> None:
    assert "install_application_updater" in RUN
    assert "entrance_stability.start()" in RUN
    assert RUN.index("entrance_stability.start()") < RUN.index("install_application_updater(window, access_controller=access_controller)")
    assert "_CHECK_DELAY_MS = 1800" in UPDATER
