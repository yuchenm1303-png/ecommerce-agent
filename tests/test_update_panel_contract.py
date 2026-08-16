from pathlib import Path


def test_formal_gui_uses_one_six_stage_topmost_update_panel() -> None:
    source = Path("gui/resilient_app_updater.py").read_text(encoding="utf-8")
    assert "class _UpdateProgressPanel(QDialog)" in source
    assert "WindowStaysOnTopHint" in source
    assert "步骤 1/6" in source
    assert "步骤 4/6" in source
    assert "listing-studio-update-sha256" in source
    assert "threading.Thread" in source
    assert '"/VERYSILENT"' in source


def test_standalone_updater_continues_visible_flow_after_gui_exit() -> None:
    entry = Path("scripts/updater_main_v2.py").read_text(encoding="utf-8")
    panel = Path("app/updater_panel.py").read_text(encoding="utf-8")
    spec = Path("packaging/Updater.spec").read_text(encoding="utf-8")
    assert "NativeUpdatePanel" in entry
    assert "步骤 4/6" in entry
    assert "步骤 5/6" in panel
    assert "步骤 6/6" in panel
    assert "_WS_EX_TOPMOST" in panel
    assert "updater_main_v2.py" in spec


def test_inno_is_hidden_behind_listing_studio_update_panel() -> None:
    gui = Path("gui/resilient_app_updater.py").read_text(encoding="utf-8")
    e2e = Path("tests/windows_updater_e2e_parent.py").read_text(encoding="utf-8")
    assert '"/VERYSILENT"' in gui
    assert '"/VERYSILENT"' in e2e
