from pathlib import Path


def test_formal_gui_uses_one_six_stage_topmost_update_panel() -> None:
    source = Path("gui/resilient_app_updater.py").read_text(encoding="utf-8")
    assert "class _UpdateProgressPanel(QDialog)" in source
    assert "WindowStaysOnTopHint" in source
    assert "步骤 1/6" in source
    assert "步骤 4/6" in source
    assert "listing-studio-update-sha256" in source
    assert "listing-studio-update-browser-close" in source
    assert '"/VERYSILENT"' in source


def test_standalone_updater_requires_real_native_panel_before_ack() -> None:
    entry = Path("scripts/updater_main_v2.py").read_text(encoding="utf-8")
    panel = Path("app/updater_panel.py").read_text(encoding="utf-8")
    assert "NativeUpdatePanel" in entry
    assert "if not panel.ready" in entry
    assert "independent updater progress panel could not be created" in entry
    assert "def ready" in panel
    assert "native update panel ready" in panel
    assert "native update panel unavailable" in panel
    assert "_WS_EX_TOPMOST" in panel


def test_standalone_panel_continues_through_lock_audit_install_and_relaunch() -> None:
    panel = Path("app/updater_panel.py").read_text(encoding="utf-8")
    assert "install tree lock audit start" in panel
    assert "步骤 5/6" in panel
    assert "步骤 6/6" in panel
    assert "native update panel shown" in panel


def test_inno_is_hidden_behind_listing_studio_update_panel() -> None:
    gui = Path("gui/resilient_app_updater.py").read_text(encoding="utf-8")
    e2e = Path("tests/windows_updater_e2e_parent.py").read_text(encoding="utf-8")
    assert '"/VERYSILENT"' in gui
    assert '"/VERYSILENT"' in e2e


def test_successful_relaunch_does_not_show_a_second_success_popup() -> None:
    source = Path("gui/resilient_app_updater.py").read_text(encoding="utf-8")
    assert "def _show_completed_update(self) -> None:" in source
    assert "Standalone updater already showed completion; relaunch stays quiet." in source
