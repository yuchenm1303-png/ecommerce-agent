from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATER_PATH = ROOT / "gui" / "app_updater.py"
UPDATER = UPDATER_PATH.read_text(encoding="utf-8")
PRESENTATION_PATH = ROOT / "gui" / "resilient_app_updater.py"
PRESENTATION = PRESENTATION_PATH.read_text(encoding="utf-8")
RUNTIME = (ROOT / "gui" / "update_runtime.py").read_text(encoding="utf-8")
CORE = (ROOT / "app" / "updater_core.py").read_text(encoding="utf-8")
ACCESS = (ROOT / "gui" / "app_access.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
PUBLISH = (ROOT / ".github" / "workflows" / "publish-update.yml").read_text(encoding="utf-8")


def test_update_sources_compile() -> None:
    for path in (
        UPDATER_PATH,
        PRESENTATION_PATH,
        ROOT / "gui" / "update_runtime.py",
        ROOT / "app" / "updater_core.py",
        ROOT / "app" / "update_browser_gate.py",
        ROOT / "app" / "updater_panel.py",
        ROOT / "scripts" / "updater_main.py",
        ROOT / "scripts" / "updater_main_v2.py",
    ):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    compile(ACCESS, str(ROOT / "gui" / "app_access.py"), "exec")


def test_formal_app_uses_one_canonical_stable_transport_with_one_presentation_layer() -> None:
    assert "import gui.app_updater as canonical" in PRESENTATION
    assert "class ApplicationUpdater(canonical.ApplicationUpdater)" in PRESENTATION
    assert "QNetworkAccessManager" not in PRESENTATION
    assert "QNetworkRequest" not in PRESENTATION
    assert "_LATEST_RELEASE_API" not in PRESENTATION
    assert "_download_portal_update" not in PRESENTATION
    assert "from gui.resilient_app_updater import install_application_updater" in RUN
    assert "install_update_runtime(app, window)" in RUN
    assert RUN.index("install_update_runtime(app, window)") < RUN.index(
        "install_application_updater(window, access_controller=access_controller)"
    )


def test_release_and_manifest_are_bound_to_exact_stable_asset() -> None:
    assert "releases/latest" in UPDATER
    assert '_MANIFEST_ASSET = "update.json"' in UPDATER
    assert "_STABLE_VERSION_RE" in UPDATER
    assert "latest != self._release_version" in UPDATER
    assert "_release_installer_digest" in UPDATER
    assert "update.json SHA-256 与 GitHub Release asset digest 不一致" in UPDATER
    assert "installer_size" in UPDATER
    assert "source_commit" in UPDATER
    assert "min_supported_version 不能高于发布版本" in UPDATER


def test_authorized_portal_delivery_is_strict_and_retried() -> None:
    assert 'payload.get("delivery") or "portal"' in UPDATER
    assert '_PORTAL_HOSTS = {"smirel.com", "www.smirel.com"}' in UPDATER
    assert "_PRIVATE_DOWNLOAD_HOSTS" in UPDATER
    assert "_download_portal_update" in UPDATER
    assert "_MAX_PORTAL_ATTEMPTS = 2" in UPDATER
    assert "_expected_github_installer_path" in UPDATER
    assert "access_controller=access_controller" in RUN


def test_download_is_durable_size_and_hash_verified() -> None:
    assert "update_download_dir()" in UPDATER
    assert "written != data.size()" in UPDATER
    assert "installer_size" in UPDATER
    assert "_MAX_INSTALLER_ATTEMPTS = 2" in UPDATER
    assert "hashlib.sha256()" in UPDATER
    assert "stream.read(1024 * 1024)" in UPDATER
    assert "更新文件 SHA-256 校验失败" in UPDATER
    assert "listing-studio-update-sha256" in PRESENTATION


def test_formal_handoff_has_no_powershell_fallback_and_requires_runtime_self_check() -> None:
    assert "prepare_standalone_updater()" in UPDATER
    assert "verify_standalone_updater" in RUNTIME
    assert '"--self-check"' in RUNTIME
    assert "powershell.exe" not in UPDATER
    assert "_launch_installer_waiter" not in UPDATER
    assert "pending-update-" in UPDATER


def test_gui_quits_only_after_updater_acknowledges_verified_job() -> None:
    assert '_HANDOFF_ACK_TIMEOUT_S = 15.0' in UPDATER
    assert 'ack.get("status") == "accepted"' in UPDATER
    assert 'int(ack.get("job_version") or 0) == JOB_VERSION' in UPDATER
    assert "if proc.poll() is not None" in UPDATER
    assert "Catch an immediate post-ACK crash" in UPDATER
    verify_block = PRESENTATION.split("def _finish_verified_install", 1)[1]
    assert "canonical._write_update_marker(version)" in verify_block
    assert "started, detail = self._handoff_installer" in verify_block
    assert "QTimer.singleShot(120, QApplication.quit)" in verify_block


def test_external_updater_owns_post_install_verification_and_relaunch() -> None:
    assert "target_version" in CORE
    assert "version_file" in CORE
    assert "installed version mismatch" in CORE
    assert "_write_completion_marker" in CORE
    assert "_launch_app(job.app_executable)" in CORE
    assert "RESULT_RELAUNCH_FAILED" in CORE
    assert "RESULT_VERSION_MISMATCH" in CORE
    assert "_consume_previous_update_result" in UPDATER


def test_installer_keeps_only_legacy_marker_relaunch_compatibility() -> None:
    assert "update-complete.json" in INSTALLER
    assert "FileExists" in INSTALLER
    assert 'Filename: "{app}\\{#MyAppExeName}"' in INSTALLER
    assert "_clear_marker(job.marker_path)" in CORE
    assert "_write_completion_marker(job)" in CORE


def test_update_ui_remains_continuous_and_visible() -> None:
    for text in (
        "发现新版本",
        "立即更新",
        "稍后",
        "步骤 1/6",
        "步骤 2/6",
        "步骤 3/6",
        "步骤 4/6",
    ):
        assert text in PRESENTATION or text in UPDATER
    assert "class _UpdateProgressPanel(QDialog)" in PRESENTATION
    assert "WindowStaysOnTopHint" in PRESENTATION
    assert "Qt.WindowModality.ApplicationModal" in PRESENTATION
    assert '"/VERYSILENT"' in PRESENTATION


def test_publish_contract_is_manual_stable_only() -> None:
    assert "workflow_dispatch:" in PUBLISH
    assert "gh release create" in PUBLISH
    assert 'channel = "stable"' in PUBLISH
    assert "installer_sha256" in PUBLISH
