from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_SOURCE = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
ACCESS_SOURCE = (ROOT / "gui" / "app_access.py").read_text(encoding="utf-8")


def test_startup_entry_sources_compile() -> None:
    compile(RUN_SOURCE, str(ROOT / "run_local_gui.py"), "exec")
    compile(ACCESS_SOURCE, str(ROOT / "gui" / "app_access.py"), "exec")


def test_paintable_startup_surface_precedes_access_and_heavy_gui_imports() -> None:
    qt_app = RUN_SOURCE.index("app = QApplication(sys.argv)")
    startup_show = RUN_SOURCE.index("startup.show()")
    access = RUN_SOURCE.index("access_session = ensure_application_access(app)")
    heavy_gui_import = RUN_SOURCE.index(
        "from gui.activity_presence import install_activity_presence"
    )

    assert qt_app < startup_show < access < heavy_gui_import


def test_stored_session_restore_keeps_authorization_policy_unchanged() -> None:
    restore_start = ACCESS_SOURCE.index("def _restore_session()")
    responsive_start = ACCESS_SOURCE.index("def _restore_session_responsive(")
    restore_source = ACCESS_SOURCE[restore_start:responsive_start]

    assert "_HTTP_TIMEOUT_SECONDS = 12" in ACCESS_SOURCE
    assert "_REVALIDATE_INTERVAL_MS = 6 * 60 * 60 * 1000" in ACCESS_SOURCE
    assert "_auth_refresh(refresh_token)" in restore_source
    assert "action=\"validate\"" in restore_source
    assert "session.grace_until > time.time()" in restore_source
    assert "except AccessError:" in restore_source


def test_stored_session_network_io_runs_off_qt_thread_but_login_stays_main_thread() -> None:
    responsive_start = ACCESS_SOURCE.index("def _restore_session_responsive(")
    friendly_start = ACCESS_SOURCE.index("def _friendly_error(")
    responsive_source = ACCESS_SOURCE[responsive_start:friendly_start]

    ensure_start = ACCESS_SOURCE.index("def ensure_application_access(")
    controller_start = ACCESS_SOURCE.index("class ApplicationAccessController")
    ensure_source = ACCESS_SOURCE[ensure_start:controller_start]

    assert "threading.Thread(" in responsive_source
    assert "outcome[\"session\"] = _restore_session()" in responsive_source
    assert "app.processEvents()" in responsive_source
    assert "restored = _restore_session_responsive(app)" in ensure_source
    assert "dialog = _LoginDialog()" in ensure_source
    assert "dialog.exec()" in ensure_source


def test_access_restore_thread_does_not_pull_gui_updater_module() -> None:
    assert "from gui.app_updater import installed_application_version" not in ACCESS_SOURCE
    assert "from app.velopack_runtime import installed_application_version" in ACCESS_SOURCE
