from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")


def test_uninstall_requests_normal_gui_close_before_file_removal() -> None:
    assert "function InitializeUninstall: Boolean;" in INSTALLER
    assert "function CloseListingStudioBeforeUninstall: Boolean;" in INSTALLER
    assert "FindWindowByWindowName(ListingStudioWindowTitle)" in INSTALLER
    assert "FindWindowByWindowName(LegacyWindowTitle)" in INSTALLER
    assert "PostMessage(Wnd, WM_CLOSE, 0, 0);" in INSTALLER
    assert "for Attempt := 1 to 50 do" in INSTALLER
    assert "Sleep(1000);" in INSTALLER
    assert "Result := False;" in INSTALLER
    assert "taskkill" not in INSTALLER.casefold()
