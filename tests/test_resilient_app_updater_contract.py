from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSPORT_PATH = ROOT / "gui" / "resilient_app_updater.py"
TRANSPORT = TRANSPORT_PATH.read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_resilient_updater_source_compiles() -> None:
    compile(TRANSPORT, str(TRANSPORT_PATH), "exec")


def test_gui_uses_resilient_updater_transport() -> None:
    assert "from gui.resilient_app_updater import install_application_updater" in RUN
    assert "install_application_updater(window, access_controller=access_controller)" in RUN


def test_release_metadata_and_asset_downloads_use_separate_http_contracts() -> None:
    assert 'accept=b"application/vnd.github+json"' in TRANSPORT
    assert 'accept=b"application/octet-stream"' in TRANSPORT
    assert 'b"X-GitHub-Api-Version"' in TRANSPORT
    assert "RedirectPolicyAttribute" in TRANSPORT
    assert "NoLessSafeRedirectPolicy" in TRANSPORT
    assert "_NETWORK_TIMEOUT_MS = 20_000" in TRANSPORT


def test_manifest_uses_asset_api_with_browser_fallback_and_bounded_retry() -> None:
    assert 'asset.get("url")' in TRANSPORT
    assert 'asset.get("browser_download_url")' in TRANSPORT
    assert '_manifest_source = "asset_api"' in TRANSPORT
    assert 'self._manifest_source = "browser_url"' in TRANSPORT
    assert "_MAX_RELEASE_ATTEMPTS = 2" in TRANSPORT
    assert "_MAX_MANIFEST_ATTEMPTS = 2" in TRANSPORT
    assert "_RETRIABLE_ERRORS" in TRANSPORT
    assert "QTimer.singleShot(_RETRY_DELAY_MS" in TRANSPORT


def test_installer_download_uses_binary_transport_not_github_json_accept() -> None:
    block = TRANSPORT.split("def _download_update", 1)[1].split("def _download_progress", 1)[0]
    assert "_asset_request(installer_url" in block
    assert "application/vnd.github+json" not in block


def test_network_failures_write_actionable_local_diagnostics() -> None:
    assert '_DIAGNOSTIC_LOG = "updater-network.jsonl"' in TRANSPORT
    assert "HttpStatusCodeAttribute" in TRANSPORT
    assert "reply.errorString()" in TRANSPORT
    assert '"qt_error_name"' in TRANSPORT
    assert '"manifest_download"' in TRANSPORT
    assert '"installer_download"' in TRANSPORT
    assert "已记录诊断" in TRANSPORT


def test_update_dialogs_stay_foreground_and_block_app_interaction() -> None:
    assert "WindowStaysOnTopHint" in TRANSPORT
    assert "Qt.WindowModality.ApplicationModal" in TRANSPORT
    assert "dialog.raise_()" in TRANSPORT
    assert "dialog.activateWindow()" in TRANSPORT
    assert "WindowCloseButtonHint, cancellable" in TRANSPORT


def test_update_flow_exposes_four_continuous_user_visible_stages() -> None:
    for stage in (
        "步骤 1/4",
        "步骤 2/4",
        "步骤 3/4",
        "步骤 4/4",
    ):
        assert stage in TRANSPORT
    assert "下载更新… " in TRANSPORT
    assert "received_mb" in TRANSPORT
    assert "total_mb" in TRANSPORT
    assert "安装完成后会自动重新打开" in TRANSPORT
    assert "更新已完成" in TRANSPORT


def test_required_update_cannot_be_dismissed_through_window_close() -> None:
    prompt = TRANSPORT.split("def _prompt_for_update", 1)[1].split("def _portal_failure", 1)[0]
    assert "WindowCloseButtonHint, False" in prompt
    assert "if required:" in prompt
    assert "QApplication.quit()" in prompt


def test_portal_fallback_is_explained_before_opening_browser() -> None:
    block = TRANSPORT.split("def _open_portal_update", 1)[1].split("def _download_update", 1)[0]
    assert "需要转到安全下载页继续更新" in block
    assert "打开下载页" in block
    assert "QDesktopServices.openUrl" in block


def test_installer_handoff_remains_visible_long_enough_to_read() -> None:
    assert "_INSTALLER_HANDOFF_MS = 1_200" in TRANSPORT
    assert "QTimer.singleShot(_INSTALLER_HANDOFF_MS, QApplication.quit)" in TRANSPORT
