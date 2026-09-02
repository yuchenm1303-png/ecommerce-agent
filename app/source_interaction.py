from __future__ import annotations

import base64
import contextvars
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


PRODUCT_READY = "PRODUCT_READY"
LOADING = "LOADING"
ORDINARY_POPUP = "ORDINARY_POPUP"
HUMAN_CHALLENGE = "HUMAN_CHALLENGE"
LOGIN_REQUIRED = "LOGIN_REQUIRED"
ACCESS_DENIED = "ACCESS_DENIED"
PARTIAL_UNKNOWN = "PARTIAL_UNKNOWN"

PAGE_STATES = {
    PRODUCT_READY,
    LOADING,
    ORDINARY_POPUP,
    HUMAN_CHALLENGE,
    LOGIN_REQUIRED,
    ACCESS_DENIED,
    PARTIAL_UNKNOWN,
}
RECOVERABLE_INTERACTIONS = {HUMAN_CHALLENGE, LOGIN_REQUIRED, ORDINARY_POPUP}
SOURCE_INTERACTION_EXIT_CODE = 75
SOURCE_OUTCOME_FILENAME = "source-outcome.json"
_CANONICAL_JPEG_DATA_URI_PREFIX = "data:image/jpeg;base64,"


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


_PAGE_STATE_PROVIDER: contextvars.ContextVar[JSONTaskProvider | None] = contextvars.ContextVar(
    "source_page_state_provider",
    default=None,
)


class SourcePageStateDecisionError(RuntimeError):
    """AI page-state output was unavailable or structurally invalid."""


@dataclass(slots=True, frozen=True)
class SourcePageState:
    """Current browser-page state decided from a fresh AI observation."""

    state: str
    reason: str
    observed_url: str = ""
    title: str = ""
    confidence: float = 0.0
    evidence_refs: tuple[str, ...] = ()

    @property
    def requires_user(self) -> bool:
        return self.state in RECOVERABLE_INTERACTIONS


def configure_source_page_state_provider(provider: JSONTaskProvider | None) -> None:
    """Bind the semantic observer for the current worker/process context."""

    _PAGE_STATE_PROVIDER.set(provider)


def current_source_page_state_provider() -> JSONTaskProvider | None:
    return _PAGE_STATE_PROVIDER.get()


def _clean(value: object, *, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _mechanical_page_observation(page: Any) -> dict[str, Any]:
    """Read bounded current UI facts without assigning semantic meaning to them."""

    payload = page.evaluate(
        r"""() => {
          const clean = (value, limit = 2400) => String(value || '')
            .replace(/\s+/g, ' ').trim().slice(0, limit);
          const visible = (el) => {
            if (!el) return false;
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
          };
          const rectOf = (el) => {
            const rect = el.getBoundingClientRect();
            return {
              x: Math.round(rect.x), y: Math.round(rect.y),
              width: Math.round(rect.width), height: Math.round(rect.height),
            };
          };
          const describeControl = (el) => ({
            tag: String(el.tagName || '').toLowerCase(),
            type: clean(el.getAttribute?.('type'), 80),
            role: clean(el.getAttribute?.('role'), 80),
            aria_label: clean(el.getAttribute?.('aria-label'), 240),
            title: clean(el.getAttribute?.('title'), 240),
            text: clean(el.innerText || el.textContent || el.value, 500),
          });
          const describeContainer = (el) => ({
            tag: String(el.tagName || '').toLowerCase(),
            role: clean(el.getAttribute?.('role'), 80),
            aria_modal: clean(el.getAttribute?.('aria-modal'), 20),
            aria_label: clean(el.getAttribute?.('aria-label'), 240),
            text: clean(el.innerText || el.textContent, 2400),
            rect: rectOf(el),
            controls: [...el.querySelectorAll('button,input,select,textarea,[role="button"],[role="checkbox"],[role="radio"]')]
              .filter(visible).slice(0, 16).map(describeControl),
          });

          const dialogs = [...document.querySelectorAll('[role="dialog"],[aria-modal="true"]')]
            .filter(visible).slice(0, 10).map(describeContainer);
          const forms = [...document.querySelectorAll('form')]
            .filter(visible).slice(0, 10).map(describeContainer);
          const iframes = [...document.querySelectorAll('iframe')]
            .filter(visible).slice(0, 12).map((frame) => ({
              src: clean(frame.getAttribute('src'), 1200),
              title: clean(frame.getAttribute('title'), 240),
              name: clean(frame.getAttribute('name'), 240),
              aria_label: clean(frame.getAttribute('aria-label'), 240),
              rect: rectOf(frame),
            }));

          const viewportArea = Math.max(1, (window.innerWidth || 1) * (window.innerHeight || 1));
          const overlays = [...document.body.querySelectorAll('*')].filter((el) => {
            if (!visible(el)) return false;
            const style = getComputedStyle(el);
            if (!['fixed', 'sticky'].includes(style.position)) return false;
            const rect = el.getBoundingClientRect();
            return (rect.width * rect.height) / viewportArea >= 0.18;
          }).slice(0, 10).map(describeContainer);

          return {
            ready_state: document.readyState,
            url: String(location.href || ''),
            title: clean(document.title, 1000),
            visible_text: String(document.body?.innerText || '').slice(0, 24000),
            viewport: {
              width: Number(window.innerWidth || 0),
              height: Number(window.innerHeight || 0),
              scroll_y: Number(window.scrollY || 0),
              document_height: Math.max(
                Number(document.body?.scrollHeight || 0),
                Number(document.documentElement?.scrollHeight || 0)
              ),
            },
            dialogs,
            forms,
            iframes,
            large_fixed_or_sticky_surfaces: overlays,
          };
        }"""
    )
    return payload if isinstance(payload, dict) else {}


def _viewport_jpeg_data_uri(page: Any) -> str:
    try:
        body = page.screenshot(type="jpeg", quality=72, full_page=False)
    except Exception:
        return ""
    if not isinstance(body, (bytes, bytearray)) or not body:
        return ""
    return _CANONICAL_JPEG_DATA_URI_PREFIX + base64.b64encode(bytes(body)).decode("ascii")


def build_source_page_state_request(
    observation: dict[str, Any],
    *,
    screenshot_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build one current-page classification request with no keyword heuristics."""

    observed_url = _clean(observation.get("url"), limit=4000)
    title = _clean(observation.get("title"), limit=1000)
    sources: list[dict[str, Any]] = [
        {
            "source_id": "page-observation",
            "source_type": "current_browser_observation",
            "kind": "text",
            "origin": observed_url or "current-browser-page",
            "content": json.dumps(
                observation,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )[:52000],
        }
    ]
    screenshot_value = str(screenshot_path or "").strip()
    image = None if screenshot_value.startswith(_CANONICAL_JPEG_DATA_URI_PREFIX) else Path(screenshot_value) if screenshot_value else None
    if screenshot_value.startswith(_CANONICAL_JPEG_DATA_URI_PREFIX) or (image is not None and image.is_file()):
        sources.append(
            {
                "source_id": "page-screenshot",
                "source_type": "current_browser_viewport",
                "kind": "image",
                "image_path": screenshot_value,
            }
        )
    allowed_refs = [str(item["source_id"]) for item in sources]

    return {
        "task": "classify_current_supplier_browser_page_state",
        "system_instruction": (
            "Judge the CURRENT visible browser page state from the supplied observation and screenshot. "
            "This is browser/UI state classification, not product interpretation. JSON only."
        ),
        "prompt_instruction": (
            "Decide what is true now. Historical redirects, URL words, element IDs, CSS class names, "
            "or stale challenge routes are not sufficient by themselves. A human challenge or login "
            "must be supported by current visible UI evidence that presently blocks normal supplier-page "
            "use. If the page is already usable after the user completed verification, return PRODUCT_READY."
        ),
        "context": {
            "observed_url": observed_url,
            "title": title,
            "allowed_evidence_refs": allowed_refs,
            "contract_version": 1,
        },
        "grounded_sources": sources,
        "rules": [
            "HUMAN_CHALLENGE means a currently visible verification/captcha/anti-bot interaction requires a human action now.",
            "LOGIN_REQUIRED means the current page is blocked until the user authenticates now.",
            "ORDINARY_POPUP means a currently visible non-authentication modal or overlay materially blocks access to the supplier product page and should be closed by the user.",
            "ACCESS_DENIED means the current page explicitly denies access and does not present a normal recoverable human interaction.",
            "LOADING means the document is visibly still transitioning/loading and a stable state cannot yet be judged.",
            "PARTIAL_UNKNOWN means current evidence is insufficient to decide safely.",
            "PRODUCT_READY means the supplier page is currently usable for capture, even if the URL/title/history contains verification or login-related words from an earlier state.",
            "Do not infer a blocker solely from URL substrings, title substrings, DOM IDs, CSS classes, iframe names, or prior state.",
            "evidence_refs may contain only exact source_id values from allowed_evidence_refs.",
            "reason must describe current visible evidence, not a keyword match.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "state": {"type": "string", "enum": sorted(PAGE_STATES)},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string", "minLength": 1, "maxLength": 1200},
                "evidence_refs": {
                    "type": "array",
                    "items": {"type": "string", "enum": allowed_refs},
                    "minItems": 1,
                    "maxItems": len(allowed_refs),
                },
            },
            "required": ["state", "confidence", "reason", "evidence_refs"],
        },
        "strict_json_schema": True,
    }


def _parse_page_state(
    raw: Any,
    *,
    observation: dict[str, Any],
    allowed_refs: set[str],
) -> SourcePageState:
    if not isinstance(raw, dict):
        raise SourcePageStateDecisionError("page-state AI response must be a JSON object")
    state = _clean(raw.get("state"), limit=80).upper()
    if state not in PAGE_STATES:
        raise SourcePageStateDecisionError(f"invalid page-state AI state={state!r}")
    reason = _clean(raw.get("reason"), limit=1200)
    if not reason:
        raise SourcePageStateDecisionError("page-state AI reason must not be empty")
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise SourcePageStateDecisionError("page-state AI confidence must be numeric") from exc
    if not 0.0 <= confidence <= 1.0:
        raise SourcePageStateDecisionError("page-state AI confidence must be within 0..1")

    refs: list[str] = []
    seen: set[str] = set()
    for value in raw.get("evidence_refs") or []:
        ref = _clean(value, limit=120)
        if not ref or ref in seen:
            continue
        if ref not in allowed_refs:
            raise SourcePageStateDecisionError(f"page-state AI cited unknown evidence ref: {ref!r}")
        seen.add(ref)
        refs.append(ref)
    if not refs:
        raise SourcePageStateDecisionError("page-state AI requires at least one evidence ref")

    return SourcePageState(
        state=state,
        reason=reason,
        observed_url=_clean(observation.get("url"), limit=4000),
        title=_clean(observation.get("title"), limit=1000),
        confidence=confidence,
        evidence_refs=tuple(refs),
    )


def classify_source_page_state(
    page: Any,
    *,
    provider: JSONTaskProvider | None = None,
    screenshot_path: str | Path | None = None,
) -> SourcePageState:
    """Classify the fresh current page with AI; Python only captures and validates."""

    observation = _mechanical_page_observation(page)
    effective_provider = provider or current_source_page_state_provider()
    if effective_provider is None:
        return SourcePageState(
            state=PRODUCT_READY,
            reason="page-state AI provider not configured; semantic blocker classification skipped",
            observed_url=_clean(observation.get("url"), limit=4000),
            title=_clean(observation.get("title"), limit=1000),
            confidence=0.0,
            evidence_refs=("page-observation",),
        )

    screenshot_source: str | Path | None = screenshot_path
    if screenshot_source is None:
        screenshot_source = _viewport_jpeg_data_uri(page)
    request = build_source_page_state_request(
        observation,
        screenshot_path=screenshot_source,
    )
    allowed_refs = {
        str(item.get("source_id") or "")
        for item in request.get("grounded_sources") or []
        if str(item.get("source_id") or "")
    }
    try:
        raw = effective_provider.extract_json(request)
    except Exception as exc:
        raise SourcePageStateDecisionError(
            f"page-state AI decision failed: {type(exc).__name__}: {exc}"
        ) from exc
    return _parse_page_state(raw, observation=observation, allowed_refs=allowed_refs)


__all__ = [
    "ACCESS_DENIED",
    "HUMAN_CHALLENGE",
    "LOGIN_REQUIRED",
    "LOADING",
    "ORDINARY_POPUP",
    "PAGE_STATES",
    "PARTIAL_UNKNOWN",
    "PRODUCT_READY",
    "RECOVERABLE_INTERACTIONS",
    "SOURCE_INTERACTION_EXIT_CODE",
    "SOURCE_OUTCOME_FILENAME",
    "SourcePageState",
    "SourcePageStateDecisionError",
    "build_source_page_state_request",
    "classify_source_page_state",
    "configure_source_page_state_provider",
    "current_source_page_state_provider",
]
