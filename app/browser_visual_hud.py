"""Shared browser visual-HUD facade.

The rendering itself stays in ``app.makro.visual_execution_hud`` so Listing
Studio continues to use the already-ported mobile Visual Agent appearance. This
module only broadens that same HUD from Step 3 writes to every visible browser
automation surface and provides best-effort status/target helpers.

Visual calls are deliberately non-authoritative: failures are logged and never
change browser business behavior.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.makro.visual_execution_hud import (
    HUD_API_KEY,
    destroy_visual_execution_hud,
    install_visual_execution_hud,
    set_visual_execution_hud_capture_safe,
)


_ENHANCE_INTERACTION_SCRIPT = r"""
key => {
  const api = window[key];
  if (!api || typeof api.interactive !== 'function') return false;
  if (api.__listingStudioBrowserEnhanced) return true;
  const original = api.interactive.bind(api);
  api.interactive = node => {
    const normal = original(node);
    if (normal) return normal;
    if (!(node instanceof Element)) return null;
    return node.closest([
      '[id^="thumbnail_"]',
      '[data-testid*="upload" i]',
      '[data-testid*="search" i]',
      '[class*="upload" i]',
      '[class*="thumbnail" i]',
      '[class*="search" i]'
    ].join(','));
  };
  api.__listingStudioBrowserEnhanced = true;
  return true;
}
"""

_STATUS_SCRIPT = r"""
([key,title,thought,phase]) => {
  const api = window[key];
  if (!api || typeof api.status !== 'function') return false;
  api.status(title, thought, phase);
  return true;
}
"""

_TARGET_SCRIPT = r"""
(el, payload) => {
  const api = window[payload.key];
  if (!api || typeof api.update !== 'function') return false;
  api.update(el, payload.phase, payload.verb, payload.detail);
  if (payload.pulse && typeof api.pulse === 'function') api.pulse();
  return true;
}
"""


def _evaluate(page: Any, script: str, payload: Any = None) -> Any:
    try:
        if payload is None:
            return page.evaluate(script)
        return page.evaluate(script, payload)
    except Exception as exc:
        print(f"GUI_BROWSER_HUD\tERROR\t{type(exc).__name__}: {exc}", flush=True)
        return None


def _page_matches_host(page: Any, host_suffix: str | None) -> bool:
    """Keep a browser HUD scoped to the automation domain that owns it."""

    if not host_suffix:
        return True
    try:
        hostname = str(urlsplit(str(page.url or "")).hostname or "").casefold()
    except Exception:
        return False
    wanted = str(host_suffix).strip().lstrip(".").casefold()
    return bool(wanted and (hostname == wanted or hostname.endswith("." + wanted)))


def _install_current(page: Any, *, title: str, thought: str, phase: int) -> bool:
    try:
        installed = bool(install_visual_execution_hud(page))
    except Exception as exc:
        print(f"GUI_BROWSER_HUD\tINSTALL_ERROR\t{type(exc).__name__}: {exc}", flush=True)
        return False
    if not installed:
        return False
    _evaluate(page, _ENHANCE_INTERACTION_SCRIPT, HUD_API_KEY)
    _evaluate(
        page,
        _STATUS_SCRIPT,
        [HUD_API_KEY, str(title), str(thought), max(0, min(4, int(phase)))],
    )
    return True


def arm_browser_visual_hud(
    page: Any,
    *,
    title: str = "浏览器自动化运行中",
    thought: str = "Listing Studio 正在读取当前页面并准备下一步操作。",
    phase: int = 1,
    host_suffix: str | None = None,
) -> bool:
    """Keep one HUD lifecycle attached to a Playwright page across navigation.

    ``host_suffix`` scopes reinjection to the browser domain owned by this
    automation.  This lets the long-lived Makro profile contain unrelated tabs
    without painting Listing Studio visuals over them.  The page receives only
    one DOMContentLoaded listener; repeated calls merely refresh the current HUD.
    """

    marker = "_listing_studio_browser_hud_armed"
    normalized_phase = max(0, min(4, int(phase)))
    status_payload = [HUD_API_KEY, str(title), str(thought), normalized_phase]
    try:
        already_armed = bool(getattr(page, marker, False))
    except Exception:
        already_armed = False

    allowed_now = _page_matches_host(page, host_suffix)
    if already_armed:
        if not allowed_now:
            return False
        # Avoid tearing down/recreating the iframe every time a harness helper
        # reacquires the same page.  Reinstall only when navigation removed it.
        if _evaluate(page, _STATUS_SCRIPT, status_payload):
            _evaluate(page, _ENHANCE_INTERACTION_SCRIPT, HUD_API_KEY)
            return True
        return _install_current(
            page,
            title=title,
            thought=thought,
            phase=normalized_phase,
        )

    try:
        setattr(page, marker, True)

        def _after_navigation() -> None:
            if not _page_matches_host(page, host_suffix):
                return
            _install_current(
                page,
                title=title,
                thought=thought,
                phase=normalized_phase,
            )

        page.on("domcontentloaded", _after_navigation)
    except Exception as exc:
        print(f"GUI_BROWSER_HUD\tARM_ERROR\t{type(exc).__name__}: {exc}", flush=True)

    if not allowed_now:
        return False
    return _install_current(
        page,
        title=title,
        thought=thought,
        phase=normalized_phase,
    )


def browser_visual_hud_status(
    page: Any,
    title: str,
    thought: str,
    *,
    phase: int = 1,
) -> None:
    """Show a high-level browser operation even when no DOM event is emitted."""

    payload = [HUD_API_KEY, str(title), str(thought), max(0, min(4, int(phase)))]
    if not _evaluate(page, _STATUS_SCRIPT, payload):
        if _install_current(page, title=title, thought=thought, phase=phase):
            _evaluate(page, _STATUS_SCRIPT, payload)


def browser_visual_hud_target(
    locator: Any,
    verb: str,
    detail: str,
    *,
    phase: int = 2,
    pulse: bool = False,
) -> None:
    """Move the visual cursor to one real locator without steering Playwright."""

    try:
        locator.evaluate(
            _TARGET_SCRIPT,
            {
                "key": HUD_API_KEY,
                "verb": str(verb),
                "detail": str(detail),
                "phase": max(0, min(4, int(phase))),
                "pulse": bool(pulse),
            },
        )
    except Exception as exc:
        print(f"GUI_BROWSER_HUD\tTARGET_ERROR\t{type(exc).__name__}: {exc}", flush=True)


def set_browser_visual_hud_capture_safe(page: Any, active: bool) -> None:
    try:
        set_visual_execution_hud_capture_safe(page, bool(active))
    except Exception as exc:
        print(
            f"GUI_BROWSER_HUD\tCAPTURE_SAFE_ERROR\t{type(exc).__name__}: {exc}",
            flush=True,
        )


def finish_browser_visual_hud(
    page: Any,
    *,
    success: bool,
    title: str | None = None,
    thought: str | None = None,
    hold_ms: int = 320,
    destroy: bool = True,
) -> None:
    final_title = title or ("浏览器任务完成" if success else "浏览器任务未完整完成")
    final_thought = thought or (
        "当前浏览器自动化阶段已完成。"
        if success
        else "当前浏览器自动化阶段已停止，请查看 Listing Studio 状态。"
    )
    browser_visual_hud_status(page, final_title, final_thought, phase=4)
    if hold_ms > 0:
        try:
            page.wait_for_timeout(int(hold_ms))
        except Exception:
            pass
    if destroy:
        try:
            destroy_visual_execution_hud(page)
        except Exception as exc:
            print(f"GUI_BROWSER_HUD\tDESTROY_ERROR\t{type(exc).__name__}: {exc}", flush=True)


__all__ = [
    "arm_browser_visual_hud",
    "browser_visual_hud_status",
    "browser_visual_hud_target",
    "finish_browser_visual_hud",
    "set_browser_visual_hud_capture_safe",
]
