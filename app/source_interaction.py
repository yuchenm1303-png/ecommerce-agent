from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PRODUCT_READY = "PRODUCT_READY"
LOADING = "LOADING"
ORDINARY_POPUP = "ORDINARY_POPUP"
HUMAN_CHALLENGE = "HUMAN_CHALLENGE"
LOGIN_REQUIRED = "LOGIN_REQUIRED"
ACCESS_DENIED = "ACCESS_DENIED"
PARTIAL_UNKNOWN = "PARTIAL_UNKNOWN"

RECOVERABLE_INTERACTIONS = {HUMAN_CHALLENGE, LOGIN_REQUIRED}
SOURCE_INTERACTION_EXIT_CODE = 75
SOURCE_OUTCOME_FILENAME = "source-outcome.json"

_CHALLENGE_TEXT = (
    "请完成验证",
    "安全验证",
    "人机验证",
    "滑块验证",
    "拖动滑块",
    "请按住滑块",
    "验证码",
    "点击验证",
    "访问过于频繁",
    "verify you are human",
    "security verification",
    "slide to verify",
    "complete verification",
    "captcha",
)
_LOGIN_TEXT = (
    "请登录",
    "登录后继续",
    "账号登录",
    "密码登录",
    "扫码登录",
    "sign in",
    "log in",
)
_DENIED_TEXT = (
    "access denied",
    "request forbidden",
    "403 forbidden",
    "访问被拒绝",
    "无权访问",
    "请求被拦截",
)
_CHALLENGE_URL = (
    "captcha",
    "nocaptcha",
    "challenge",
    "verification",
    "verify",
    "security-check",
    "secdev",
    "punish",
)
_LOGIN_URL = (
    "/login",
    "/signin",
    "passport.",
    "login.",
    "member/signin",
)


@dataclass(slots=True, frozen=True)
class SourcePageState:
    """Mechanical browser-page state; never a product-semantic judgement."""

    state: str
    reason: str
    observed_url: str = ""
    title: str = ""

    @property
    def requires_user(self) -> bool:
        return self.state in RECOVERABLE_INTERACTIONS


def _fold(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _first_marker(text: str, markers: tuple[str, ...]) -> str:
    return next((marker for marker in markers if marker.casefold() in text), "")


def classify_source_page_state(
    *,
    url: object = "",
    title: object = "",
    visible_text: object = "",
    hints: dict[str, Any] | None = None,
) -> SourcePageState:
    """Classify only transport/UI state from deterministic browser evidence.

    This function deliberately does not infer whether a product is the right item,
    whether its attributes are sufficient, or what any product field means. Those
    remain downstream AI responsibilities. It only separates a usable page shell
    from login/security/interstitial states so automation can suspend safely.
    """

    observed_url = str(url or "").strip()
    page_title = " ".join(str(title or "").split())
    text = _fold(visible_text)[:30_000]
    folded_url = observed_url.casefold()
    folded_title = page_title.casefold()
    meta = hints if isinstance(hints, dict) else {}

    challenge_marker = _first_marker(text, _CHALLENGE_TEXT)
    challenge_url = _first_marker(folded_url, _CHALLENGE_URL)
    if bool(meta.get("challenge_visible")) or challenge_marker or challenge_url:
        marker = challenge_marker or challenge_url or str(meta.get("challenge_marker") or "browser challenge")
        return SourcePageState(
            HUMAN_CHALLENGE,
            f"source browser requires human verification ({marker})",
            observed_url,
            page_title,
        )

    login_marker = _first_marker(text, _LOGIN_TEXT)
    login_url = _first_marker(folded_url, _LOGIN_URL)
    if bool(meta.get("login_visible")) or login_url or (
        bool(meta.get("password_visible")) and login_marker
    ):
        marker = login_marker or login_url or "visible login form"
        return SourcePageState(
            LOGIN_REQUIRED,
            f"source browser requires account login ({marker})",
            observed_url,
            page_title,
        )

    denied_marker = _first_marker(text, _DENIED_TEXT)
    if bool(meta.get("access_denied_visible")) or denied_marker or (
        "403" in folded_title and len(text) < 2_000
    ):
        marker = denied_marker or "access denied page"
        return SourcePageState(
            ACCESS_DENIED,
            f"supplier access was explicitly denied ({marker})",
            observed_url,
            page_title,
        )

    if bool(meta.get("ordinary_popup_visible")):
        return SourcePageState(
            ORDINARY_POPUP,
            "ordinary dismissible page dialog is covering the supplier page",
            observed_url,
            page_title,
        )

    ready_state = str(meta.get("ready_state") or "").strip().casefold()
    if ready_state and ready_state != "complete" and len(text) < 1_200:
        return SourcePageState(
            LOADING,
            f"document.readyState={ready_state}",
            observed_url,
            page_title,
        )

    if not text and not page_title:
        return SourcePageState(
            PARTIAL_UNKNOWN,
            "browser page exposes no visible document evidence",
            observed_url,
            page_title,
        )

    return SourcePageState(
        PRODUCT_READY,
        "no login, challenge, access-denied, or modal blocker detected",
        observed_url,
        page_title,
    )


def dismiss_ordinary_source_popup(page: Any) -> bool:
    """Dismiss only an unambiguous benign modal close control.

    Authentication/security dialogs are explicitly excluded. The routine never
    solves, clicks through, or otherwise attempts to bypass a human challenge.
    """

    try:
        return bool(
            page.evaluate(
                r"""() => {
                  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
                  const visible = (el) => {
                    if (!el) return false;
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden'
                      && rect.width > 0 && rect.height > 0;
                  };
                  const blocked = /(captcha|verify|verification|安全验证|人机验证|滑块|验证码|请登录|登录后继续|sign\s*in|log\s*in)/i;
                  const dialogs = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')]
                    .filter(visible);
                  for (const dialog of dialogs) {
                    const text = clean(dialog.innerText || dialog.textContent);
                    if (blocked.test(text)) continue;
                    const candidates = [...dialog.querySelectorAll('button,[role="button"],[aria-label],[title]')]
                      .filter(visible);
                    for (const candidate of candidates) {
                      const label = clean(
                        candidate.getAttribute('aria-label')
                        || candidate.getAttribute('title')
                        || candidate.innerText
                        || candidate.textContent
                      );
                      const className = clean(candidate.className);
                      if (/^(×|x|close|关闭|稍后再说|not now)$/i.test(label)
                          || /(^|[-_\s])(close|dismiss)([-_\s]|$)/i.test(className)) {
                        candidate.click();
                        return true;
                      }
                    }
                  }
                  return false;
                }"""
            )
        )
    except Exception:
        return False


__all__ = [
    "ACCESS_DENIED",
    "HUMAN_CHALLENGE",
    "LOGIN_REQUIRED",
    "LOADING",
    "ORDINARY_POPUP",
    "PARTIAL_UNKNOWN",
    "PRODUCT_READY",
    "RECOVERABLE_INTERACTIONS",
    "SOURCE_INTERACTION_EXIT_CODE",
    "SOURCE_OUTCOME_FILENAME",
    "SourcePageState",
    "classify_source_page_state",
    "dismiss_ordinary_source_popup",
]
