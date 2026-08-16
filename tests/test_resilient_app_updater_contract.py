from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PATH = ROOT / "gui" / "app_updater.py"
CANONICAL = CANONICAL_PATH.read_text(encoding="utf-8")
PRESENTATION_PATH = ROOT / "gui" / "resilient_app_updater.py"
PRESENTATION = PRESENTATION_PATH.read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_resilient_module_is_presentation_only_over_canonical_transport() -> None:
    compile(PRESENTATION, str(PRESENTATION_PATH), "exec")
    assert "import gui.app_updater as canonical" in PRESENTATION
    assert "class ApplicationUpdater(canonical.ApplicationUpdater)" in PRESENTATION
    assert "QNetworkAccessManager" not in PRESENTATION
    assert "QNetworkRequest" not in PRESENTATION
    assert "_LATEST_RELEASE_API" not in PRESENTATION
    assert "_download_portal_update" not in PRESENTATION
    assert "from gui.resilient_app_updater import install_application_updater" in RUN


def test_presentation_keeps_expensive_pre_handoff_work_off_qt_thread() -> None:
    assert "listing-studio-update-sha256" in PRESENTATION
    assert "listing-studio-update-browser-close" in PRESENTATION
    assert "threading.Thread" in PRESENTATION
    assert "_checksum_ready" in PRESENTATION
    assert "_browser_close_ready" in PRESENTATION


def test_presentation_owns_managed_browser_gate_not_arbitrary_edge_shutdown() -> None:
    assert "close_managed_browser" in PRESENTATION
    assert "DEFAULT_CDP_PORT" in PRESENTATION
    assert "poll_timer.stop()" in PRESENTATION
    assert "poll_timer.start()" in PRESENTATION
    assert "不会关闭其他普通 Edge 窗口" in PRESENTATION
    assert "taskkill" not in PRESENTATION.lower()


def test_canonical_updater_owns_resilient_network_contract() -> None:
    assert 'accept=b"application/vnd.github+json"' in CANONICAL
    assert 'accept=b"application/octet-stream"' in CANONICAL
    assert 'b"X-GitHub-Api-Version"' in CANONICAL
    assert "RedirectPolicyAttribute" in CANONICAL
    assert "NoLessSafeRedirectPolicy" in CANONICAL
    assert "_NETWORK_TIMEOUT_MS = 20_000" in CANONICAL
    assert "_MAX_RELEASE_ATTEMPTS = 2" in CANONICAL
    assert "_MAX_MANIFEST_ATTEMPTS = 2" in CANONICAL
    assert "_MAX_PORTAL_ATTEMPTS = 2" in CANONICAL
    assert "_MAX_INSTALLER_ATTEMPTS = 2" in CANONICAL


def test_network_failures_write_actionable_diagnostics() -> None:
    assert '_DIAGNOSTIC_LOG = "updater-network.jsonl"' in CANONICAL
    assert "HttpStatusCodeAttribute" in CANONICAL
    assert "reply.errorString()" in CANONICAL
    assert '"release_metadata"' in CANONICAL
    assert '"manifest_download"' in CANONICAL
    assert '"portal_authorization"' in CANONICAL
    assert '"installer_download"' in CANONICAL
    assert "已记录诊断" in CANONICAL


def test_update_dialogs_stay_foreground_and_required_update_cannot_close() -> None:
    assert "WindowStaysOnTopHint" in CANONICAL
    assert "Qt.WindowModality.ApplicationModal" in CANONICAL
    assert "dialog.raise_()" in CANONICAL
    assert "dialog.activateWindow()" in CANONICAL
    prompt = CANONICAL.split("def _prompt_for_update", 1)[1].split(
        "def _download_portal_update", 1
    )[0]
    assert "WindowCloseButtonHint, False" in prompt
    assert "if required:" in prompt


def test_formal_handoff_is_acknowledged_not_fire_and_forget() -> None:
    block = CANONICAL.split("def _handoff_installer", 1)[1].split(
        "def _verify_and_install", 1
    )[0]
    assert "prepare_standalone_updater()" in block
    assert "ack_path" in block
    assert "proc.poll()" in block
    assert "QApplication.processEvents()" in block
    assert "worker_pids=owned_qprocess_pids(self.window)" in block
    assert "powershell" not in block.lower()
