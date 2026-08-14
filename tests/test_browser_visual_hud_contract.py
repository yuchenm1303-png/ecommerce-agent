from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HUD = (ROOT / "app" / "browser_visual_hud.py").read_text(encoding="utf-8")
SESSION = (ROOT / "app" / "browser_session.py").read_text(encoding="utf-8")
SOURCE = (ROOT / "app" / "source_capture.py").read_text(encoding="utf-8")
MOBILE_PORT = (ROOT / "app" / "makro" / "visual_execution_hud.py").read_text(encoding="utf-8")
PHOTOS = (ROOT / "app" / "makro" / "photos.py").read_text(encoding="utf-8")


def test_shared_browser_hud_reuses_existing_visual_agent_renderer() -> None:
    assert "from app.makro.visual_execution_hud import" in HUD
    assert "install_visual_execution_hud" in HUD
    assert "HUD_API_KEY" in HUD
    assert "mouseCursorShape" in MOBILE_PORT
    assert "edge-aurora" in MOBILE_PORT
    assert "pointer-events:none" in MOBILE_PORT


def test_hud_recognizes_upload_and_search_custom_surfaces() -> None:
    for token in (
        '[id^="thumbnail_"]',
        '[data-testid*="upload" i]',
        '[data-testid*="search" i]',
        '[class*="upload" i]',
        '[class*="thumbnail" i]',
        '[class*="search" i]',
    ):
        assert token in HUD
    assert "slot.click(timeout=1_500, force=True)" in PHOTOS
    assert "upload_button.click(timeout=1_500, force=True)" in PHOTOS
    assert "set_input_files" in PHOTOS


def test_makro_harness_owns_one_domain_scoped_hud_lifecycle() -> None:
    assert 'host_suffix=_MAKRO_HUD_HOST' in SESSION
    assert '_MAKRO_HUD_HOST = "seller.makro.co.za"' in SESSION
    assert 'context.on("page", self._watch_visual_page)' in SESSION
    assert 'page.on("domcontentloaded", _after_navigation)' in HUD
    # Navigation reinjection belongs to the shared facade, not a second Harness listener.
    assert 'page.on("domcontentloaded", lambda: self._show_visual_hud(page))' not in SESSION
    assert "hostname.endswith(\".\" + wanted)" in HUD


def test_supplier_capture_shows_hud_without_polluting_evidence() -> None:
    for token in (
        "正在打开商品链接",
        "正在展开商品页面",
        "正在扫描商品页面",
        "正在提取商品信息",
        "正在检索详情资源",
        "正在整理商品图片",
        "正在生成页面证据",
        "商品页信息提取完成",
    ):
        assert token in SOURCE
    hide = SOURCE.index("set_browser_visual_hud_capture_safe(page, True)")
    screenshot = SOURCE.index("_screenshot_with_navigation_retry(", hide)
    restore = SOURCE.index("set_browser_visual_hud_capture_safe(page, False)", screenshot)
    assert hide < screenshot < restore


def test_visual_layer_remains_non_authoritative() -> None:
    assert "Visual calls are deliberately non-authoritative" in HUD
    assert "browser_visual_hud_target" in HUD
    assert "locator.evaluate(" in HUD
    assert "locator.click(" not in HUD
    assert "locator.fill(" not in HUD
    assert "Send to QC" not in HUD
