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
    block = TRANSPORT.split("def _download_update", 1)[1].split("def _download_finished_resilient", 1)[0]
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
