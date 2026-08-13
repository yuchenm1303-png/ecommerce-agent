from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATER = (ROOT / "gui" / "app_updater.py").read_text(encoding="utf-8")


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


def test_updater_source_compiles() -> None:
    compile(UPDATER, str(ROOT / "gui" / "app_updater.py"), "exec")
