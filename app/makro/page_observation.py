"""Bounded page observation for Makro runtime recovery.

The observation is evidence only. It never clicks, navigates, reloads or mutates
the portal. Recovery AI receives this snapshot and can only recommend one of the
actions defined in ``runtime_contract``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .portal_adapter import MakroPortalAdapter


_INTERACTIVE_SELECTOR = "button, [role='button'], a[href], input, select, textarea"
_OVERLAY_SELECTORS = (
    ".joyride-overlay",
    ".react-joyride__overlay",
    "[class*='joyride-overlay']",
    "[role='dialog']",
    "[aria-modal='true']",
    ".modal",
    "[class*='modal']",
)
_HUMAN_MARKERS = (
    "verify you are human",
    "human verification",
    "captcha",
    "cloudflare",
    "are you a robot",
    "i'm not a robot",
    "im not a robot",
    "人机验证",
    "安全验证",
    "机器人验证",
)
_LOGIN_MARKERS = (
    "sign in to your account",
    "log in to your account",
    "login to your account",
    "seller login",
    "登录您的帐户",
    "登录你的帐户",
)


@dataclass(slots=True, frozen=True)
class InteractiveElement:
    element_id: str
    tag: str
    role: str
    label: str
    element_type: str = ""
    test_id: str = ""
    href: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "element_id": self.element_id,
            "tag": self.tag,
            "role": self.role,
            "label": self.label,
            "type": self.element_type,
            "test_id": self.test_id,
            "href": self.href,
        }


@dataclass(slots=True, frozen=True)
class PageObservation:
    url: str
    title: str
    detected_stage: str
    expected_stage: str = ""
    target_id: str = ""
    body_excerpt: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    overlays: tuple[str, ...] = ()
    interactive_elements: tuple[InteractiveElement, ...] = ()
    login_required: bool = False
    human_verification: bool = False
    screenshot_path: str = ""

    def as_ai_context(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "detected_stage": self.detected_stage,
            "expected_stage": self.expected_stage,
            "target_id": self.target_id,
            "body_excerpt": self.body_excerpt,
            "diagnostics": self.diagnostics,
            "overlays": list(self.overlays),
            "interactive_elements": [item.as_dict() for item in self.interactive_elements],
            "login_required": self.login_required,
            "human_verification": self.human_verification,
        }

    def element_ids(self) -> set[str]:
        return {item.element_id for item in self.interactive_elements}


def _safe_text(locator: Any, *, limit: int = 180) -> str:
    for getter in (
        lambda: locator.inner_text(timeout=350),
        lambda: locator.get_attribute("aria-label"),
        lambda: locator.get_attribute("title"),
        lambda: locator.get_attribute("placeholder"),
        lambda: locator.get_attribute("value"),
    ):
        try:
            value = str(getter() or "").strip()
        except Exception:
            continue
        if value:
            return " ".join(value.split())[:limit]
    return ""


def _interactive_elements(page: Any, *, limit: int = 80) -> tuple[InteractiveElement, ...]:
    try:
        locator = page.locator(_INTERACTIVE_SELECTOR)
        count = min(int(locator.count()), int(limit))
    except Exception:
        return ()

    output: list[InteractiveElement] = []
    for index in range(count):
        item = locator.nth(index)
        try:
            if not item.is_visible():
                continue
        except Exception:
            continue
        try:
            tag = str(item.evaluate("(el) => el.tagName.toLowerCase()") or "")
        except Exception:
            tag = ""
        try:
            role = str(item.get_attribute("role") or "")
            element_type = str(item.get_attribute("type") or "")
            test_id = str(item.get_attribute("data-testid") or "")
            href = str(item.get_attribute("href") or "")
        except Exception:
            role = element_type = test_id = href = ""
        output.append(
            InteractiveElement(
                element_id=f"E{len(output) + 1:03d}",
                tag=tag,
                role=role,
                label=_safe_text(item),
                element_type=element_type,
                test_id=test_id,
                href=href[:260],
            )
        )
    return tuple(output)


def _visible_overlays(page: Any) -> tuple[str, ...]:
    output: list[str] = []
    for selector in _OVERLAY_SELECTORS:
        try:
            locator = page.locator(selector)
            count = min(int(locator.count()), 6)
        except Exception:
            continue
        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
            except Exception:
                continue
            label = _safe_text(item, limit=120)
            output.append(f"{selector}::{label}" if label else selector)
            if len(output) >= 12:
                return tuple(output)
    return tuple(output)


def _body_excerpt(page: Any, *, limit: int = 7000) -> str:
    try:
        text = str(page.locator("body").inner_text(timeout=700) or "")
    except Exception:
        return ""
    return " ".join(text.split())[:limit]


def _detect_stage(page: Any) -> tuple[str, dict[str, Any]]:
    try:
        adapter = MakroPortalAdapter(page)
        stage = adapter.detect_stage()
        diagnostics = adapter.diagnostics()
        stage_value = str(getattr(stage, "value", stage) or "")
        return stage_value, dict(diagnostics or {})
    except Exception as exc:
        return "", {"observation_error": type(exc).__name__}


def observe_page(
    page: Any,
    *,
    expected_stage: str = "",
    target_id: str = "",
    output_dir: str | Path | None = None,
) -> PageObservation:
    """Capture one bounded, read-only browser observation."""

    body = _body_excerpt(page)
    lowered = body.casefold()
    overlays = _visible_overlays(page)
    detected_stage, diagnostics = _detect_stage(page)

    password_visible = False
    try:
        password_visible = any(
            page.locator('input[type="password"]').nth(index).is_visible()
            for index in range(min(int(page.locator('input[type="password"]').count()), 4))
        )
    except Exception:
        password_visible = False

    login_required = password_visible or any(marker in lowered for marker in _LOGIN_MARKERS)
    human_verification = any(marker in lowered for marker in _HUMAN_MARKERS)

    screenshot_path = ""
    if output_dir is not None:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        target = root / "runtime-observation.png"
        try:
            page.screenshot(path=str(target), full_page=False, animations="disabled")
            screenshot_path = str(target.resolve())
        except Exception:
            screenshot_path = ""

    try:
        title = str(page.title() or "")
    except Exception:
        title = ""

    return PageObservation(
        url=str(getattr(page, "url", "") or ""),
        title=title,
        detected_stage=detected_stage,
        expected_stage=str(expected_stage or ""),
        target_id=str(target_id or ""),
        body_excerpt=body,
        diagnostics=diagnostics,
        overlays=overlays,
        interactive_elements=_interactive_elements(page),
        login_required=login_required,
        human_verification=human_verification,
        screenshot_path=screenshot_path,
    )


__all__ = ["InteractiveElement", "PageObservation", "observe_page"]
