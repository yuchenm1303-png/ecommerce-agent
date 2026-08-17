from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPDATER = (ROOT / "gui" / "app_updater.py").read_text(encoding="utf-8")
PANEL = (ROOT / "gui" / "update_panel.py").read_text(encoding="utf-8")
CRASH_CORE = (ROOT / "app" / "crash_diagnostics.py").read_text(encoding="utf-8")
CRASH_UI = (ROOT / "gui" / "crash_diagnostics_ui.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
TELEMETRY = (ROOT / "supabase" / "functions" / "portal-telemetry" / "index.ts").read_text(encoding="utf-8")


def test_update_ui_exposes_current_version_and_manual_check() -> None:
    assert 'f"v{self.current_version}  ·  STABLE"' in UPDATER
    assert 'setObjectName("appVersionBadge")' in UPDATER
    assert 'QPushButton("检查更新"' in UPDATER
    assert 'setObjectName("checkUpdateButton")' in UPDATER
    assert 'check_button.clicked.connect(self.manual_check_for_updates)' in UPDATER
    assert '"检查中…" if busy else "检查更新"' in UPDATER
    assert 'f"当前已是最新版本 v{self.current_version}。"' in UPDATER


def test_installed_app_checks_stable_updates_at_startup_and_while_running() -> None:
    assert '_CHECK_DELAY_MS = 1800' in UPDATER
    assert '_AUTO_CHECK_INTERVAL_MS = 60 * 60 * 1000' in UPDATER
    assert 'QTimer.singleShot(_CHECK_DELAY_MS, self.check_for_updates)' in UPDATER
    assert 'self._auto_timer.timeout.connect(self.check_for_updates)' in UPDATER
    assert 'self._auto_timer.start()' in UPDATER


def test_periodic_check_does_not_repeat_the_same_update_prompt() -> None:
    assert 'self._last_prompted_version' in UPDATER
    assert 'latest == self._last_prompted_version' in UPDATER
    assert 'manual = self._manual_check' in UPDATER
    assert 'if not manual and latest == self._last_prompted_version' in UPDATER


def test_updater_uses_premium_foreground_panels_and_download_progress() -> None:
    assert "UpdateOfferDialog" in UPDATER
    assert "UpdateProgressDialog" in UPDATER
    assert "UpdateMessageDialog" in UPDATER
    assert "QProgressDialog" not in UPDATER
    assert "_download_progress" in UPDATER
    assert "WindowStaysOnTopHint" in PANEL
    assert 'setObjectName("updatePanelCard")' in PANEL
    assert 'QPushButton("立即更新"' in PANEL
    assert 'setObjectName("updateProgress")' in PANEL
    assert "Velopack" in PANEL


def test_crash_diagnostics_capture_previous_abnormal_exit_and_offer_one_click_report() -> None:
    assert "faulthandler.enable" in CRASH_CORE
    assert "sys.excepthook = _handle_unhandled" in CRASH_CORE
    assert '"pending-report.json"' in CRASH_CORE
    assert 'previous_stage == "velopack_handoff"' in CRASH_CORE
    assert "start_crash_diagnostics()" in RUN
    assert 'mark_startup_stage("running")' in RUN
    assert "offer_pending_crash_report" in RUN
    assert 'QPushButton("发送诊断报告"' in CRASH_UI
    assert '"action": "diagnostic"' in CRASH_UI
    assert "acknowledge_pending_report" in CRASH_UI
    assert '["session_start", "heartbeat", "event", "session_end", "diagnostic", "task_audit", "system_sample"].includes(action)' in TELEMETRY
    assert 'const DIAGNOSTIC_TABLE = "listing_diagnostic_reports"' in TELEMETRY
    assert "MAX_DIAGNOSTIC_BYTES" in TELEMETRY
    assert "report_code" in TELEMETRY


def test_crash_report_privacy_contract_excludes_business_content() -> None:
    assert "supplier" not in CRASH_CORE.lower()
    assert "product" not in CRASH_CORE.lower()
    assert "prompt" not in CRASH_CORE.lower()
    assert "password" not in CRASH_CORE.lower()
    assert "不会发送商品链接、账号密码、AI 内容或客户文件" in CRASH_UI


def test_updater_presentation_sources_compile() -> None:
    compile(UPDATER, str(ROOT / "gui" / "app_updater.py"), "exec")
    compile(PANEL, str(ROOT / "gui" / "update_panel.py"), "exec")
    compile(CRASH_CORE, str(ROOT / "app" / "crash_diagnostics.py"), "exec")
    compile(CRASH_UI, str(ROOT / "gui" / "crash_diagnostics_ui.py"), "exec")
    compile(RUN, str(ROOT / "run_local_gui.py"), "exec")
