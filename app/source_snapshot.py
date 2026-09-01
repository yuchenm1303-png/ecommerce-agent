from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SourceCaptureError(RuntimeError):
    pass


class SourceAccessBlocked(SourceCaptureError):
    pass


class SourceInteractionRequired(SourceCaptureError):
    """Recoverable browser interaction that must be completed by the user."""

    def __init__(
        self,
        kind: str,
        reason: str,
        *,
        observed_url: str = "",
        title: str = "",
    ) -> None:
        self.kind = str(kind or "HUMAN_CHALLENGE").strip().upper()
        self.reason = str(reason or "source browser requires user interaction").strip()
        self.observed_url = str(observed_url or "").strip()
        self.title = " ".join(str(title or "").split())
        super().__init__(self.reason)


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@dataclass(slots=True, frozen=True)
class SnapshotTableRow:
    key: str
    value: str
    table_index: int
    row_index: int

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "SnapshotTableRow":
        return cls(
            key=_clean_text(payload.get("key")),
            value=_clean_text(payload.get("value")),
            table_index=int(payload.get("table_index") or 0),
            row_index=int(payload.get("row_index") or 0),
        )


@dataclass(slots=True)
class SourceSnapshot:
    requested_url: str
    final_url: str
    title: str
    captured_at: str
    visible_text: str = ""
    table_rows: list[SnapshotTableRow] = field(default_factory=list)
    json_ld: list[Any] = field(default_factory=list)
    embedded_data: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 3,
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "title": self.title,
            "captured_at": self.captured_at,
            "visible_text": self.visible_text,
            "table_rows": [
                {
                    "key": row.key,
                    "value": row.value,
                    "table_index": row.table_index,
                    "row_index": row.row_index,
                }
                for row in self.table_rows
            ],
            "json_ld": self.json_ld,
            "embedded_data": list(self.embedded_data),
            "image_urls": list(self.image_urls),
            "meta": self.meta,
            "warnings": self.warnings,
        }

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "SourceSnapshot":
        if not isinstance(payload, dict):
            raise ValueError("source snapshot 必须是 JSON object。")
        return cls(
            requested_url=str(payload.get("requested_url") or "").strip(),
            final_url=str(payload.get("final_url") or "").strip(),
            title=_clean_text(payload.get("title")),
            captured_at=str(payload.get("captured_at") or "").strip(),
            visible_text=str(payload.get("visible_text") or ""),
            table_rows=[
                SnapshotTableRow.from_mapping(item)
                for item in payload.get("table_rows") or []
                if isinstance(item, dict)
            ],
            json_ld=list(payload.get("json_ld") or []),
            embedded_data=[
                str(item).strip()
                for item in payload.get("embedded_data") or []
                if str(item).strip()
            ],
            image_urls=[
                str(item).strip()
                for item in payload.get("image_urls") or []
                if str(item).strip()
            ],
            meta={str(k): _clean_text(v) for k, v in (payload.get("meta") or {}).items()},
            warnings=[str(item) for item in payload.get("warnings") or []],
        )


def source_snapshot_from_json(path: str | Path) -> SourceSnapshot:
    source = Path(path)
    return SourceSnapshot.from_mapping(json.loads(source.read_text(encoding="utf-8")))


def write_source_snapshot(snapshot: SourceSnapshot, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(snapshot.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def _bounded_embedded_data(items: list[object], *, max_chars: int = 80_000) -> tuple[list[str], bool]:
    output: list[str] = []
    seen: set[str] = set()
    used = 0
    truncated = False
    for raw in items:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        if used + len(value) > max_chars:
            remaining = max_chars - used
            if remaining > 200:
                output.append(value[:remaining])
            truncated = True
            break
        output.append(value)
        used += len(value)
    return output, truncated


def capture_page_snapshot(
    page: Any,
    *,
    requested_url: str,
    max_visible_text_chars: int = 120_000,
) -> SourceSnapshot:
    """Mechanically capture raw product-page evidence without interpreting it.

    Human verification/login is a recoverable browser lifecycle state, not a bad
    product. A conservative benign-modal close is attempted first; security/login
    dialogs are never clicked through or solved by automation.
    """

    from .source_interaction import (
        ACCESS_DENIED,
        RECOVERABLE_INTERACTIONS,
        classify_source_page_state,
        dismiss_ordinary_source_popup,
    )

    if dismiss_ordinary_source_popup(page):
        try:
            page.wait_for_timeout(180)
        except Exception:
            pass

    payload = page.evaluate(
        r"""() => {
          const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
          const visible = (el) => {
            if (!el) return false;
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 0 && rect.height > 0;
          };
          const rows = [];
          const rowSeen = new Set();
          const pushRow = (key, value, tableIndex, rowIndex) => {
            key = clean(key);
            value = clean(value);
            if (!key || !value || key.length > 160 || value.length > 1200) return;
            const fingerprint = `${key}\u0000${value}`;
            if (rowSeen.has(fingerprint)) return;
            rowSeen.add(fingerprint);
            rows.push({key, value, table_index: tableIndex, row_index: rowIndex});
          };

          [...document.querySelectorAll('table')].forEach((table, tableIndex) => {
            [...table.querySelectorAll('tr')].forEach((tr, rowIndex) => {
              if (!visible(tr)) return;
              const cells = [...tr.querySelectorAll('th,td')].map((cell) => clean(cell.innerText || cell.textContent));
              if (cells.length >= 2) pushRow(cells[0], cells.slice(1).join(' | '), tableIndex + 1, rowIndex + 1);
            });
          });

          [...document.querySelectorAll('dl')].forEach((dl, tableIndex) => {
            const dts = [...dl.querySelectorAll(':scope > dt')];
            dts.forEach((dt, rowIndex) => {
              const dd = dt.nextElementSibling;
              if (!dd || dd.tagName.toLowerCase() !== 'dd' || !visible(dt) || !visible(dd)) return;
              pushRow(dt.innerText || dt.textContent, dd.innerText || dd.textContent, 1000 + tableIndex + 1, rowIndex + 1);
            });
          });

          // Marketplace attribute panels frequently use simple <li><p>key</p><p>value</p></li>
          // structures. Capture only exact two-child rows; no semantic guessing is performed.
          [...document.querySelectorAll('li')].forEach((li, rowIndex) => {
            if (!visible(li)) return;
            const direct = [...li.children]
              .map((child) => clean(child.innerText || child.textContent))
              .filter(Boolean);
            if (direct.length === 2) pushRow(direct[0], direct[1], 2001, rowIndex + 1);
          });

          const jsonLd = [];
          [...document.querySelectorAll('script[type="application/ld+json"]')].forEach((script) => {
            try { jsonLd.push(JSON.parse(script.textContent || '')); } catch (_) {}
          });

          const embedded = [];
          const pushEmbedded = (value) => {
            const text = clean(value);
            if (text && text.length <= 6000 && embedded.length < 160) embedded.push(text);
          };

          const variantSelectors = [
            '[data-sku-id]', '[data-skuid]', '[data-sku]', '[data-spec-id]', '[data-specid]',
            '[role="option"]', '[role="radio"]', 'input[type="radio"]',
            '[class*="sku"]', '[class*="Sku"]', '[class*="spec"]', '[class*="Spec"]'
          ].join(',');
          [...document.querySelectorAll(variantSelectors)].slice(0, 400).forEach((el) => {
            const attrs = {};
            [...(el.attributes || [])].forEach((attr) => {
              if ((attr.name.startsWith('data-') || ['value', 'title', 'aria-label', 'aria-checked'].includes(attr.name))
                  && String(attr.value || '').length <= 1000) {
                attrs[attr.name] = attr.value;
              }
            });
            const text = clean(el.innerText || el.textContent || el.value || '');
            if (text || Object.keys(attrs).length) pushEmbedded(JSON.stringify({tag: el.tagName, text, attrs}));
          });

          // Only product identity, variant and detail-document structures belong
          // here. Generic words such as length/width occur throughout JavaScript
          // libraries and previously pulled tens of thousands of noise chars.
          const marker = /(skuId|sku2|skuMap|skuProps|specId|offerId|detailUrl)/ig;
          [...document.scripts].forEach((script) => {
            if (script.type === 'application/ld+json') return;
            const raw = String(script.textContent || '');
            if (!raw || raw.length < 2) return;
            marker.lastIndex = 0;
            let match;
            const ranges = [];
            while ((match = marker.exec(raw)) && ranges.length < 40) {
              const next = {
                start: Math.max(0, match.index - 1400),
                end: Math.min(raw.length, match.index + 2600),
              };
              const previous = ranges[ranges.length - 1];
              if (previous && next.start <= previous.end) previous.end = Math.max(previous.end, next.end);
              else ranges.push(next);
            }
            ranges.slice(0, 10).forEach((range) => pushEmbedded(raw.slice(range.start, range.end)));
          });

          const imageUrls = [];
          const imageSeen = new Set();
          const pushImage = (raw) => {
            const value = clean(raw);
            if (!value || value.startsWith('data:') || value.startsWith('blob:')) return;
            try {
              const absolute = new URL(value, document.baseURI).href;
              if (!/^https?:/i.test(absolute) || imageSeen.has(absolute)) return;
              imageSeen.add(absolute);
              imageUrls.push(absolute);
            } catch (_) {}
          };
          [...document.images].forEach((img) => {
            if (!visible(img)) return;
            const nw = Number(img.naturalWidth || 0);
            const nh = Number(img.naturalHeight || 0);
            if (Math.max(nw, nh) < 280) return;
            pushImage(img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src'));
          });

          const visibleFrames = [...document.querySelectorAll('iframe')].filter(visible);
          const challengePattern = /(captcha|nocaptcha|challenge|verify|verification|security|secdev|slider|滑块|验证)/i;
          const loginPattern = /(login|signin|passport|登录)/i;
          const challengeVisible = visibleFrames.some((frame) => challengePattern.test(
            `${frame.src || ''} ${frame.id || ''} ${frame.name || ''} ${frame.className || ''}`
          )) || [...document.querySelectorAll('[id],[class],[data-testid]')].filter(visible).slice(0, 1200).some((el) => {
            const markerText = `${el.id || ''} ${el.className || ''} ${el.getAttribute('data-testid') || ''}`;
            return challengePattern.test(markerText);
          });
          const passwordVisible = [...document.querySelectorAll('input[type="password"]')].some(visible);
          const loginVisible = [...document.querySelectorAll('form,[role="dialog"],iframe')].filter(visible).some((el) => {
            const markerText = `${el.id || ''} ${el.className || ''} ${el.getAttribute?.('name') || ''} ${el.getAttribute?.('src') || ''}`;
            const text = clean(el.innerText || el.textContent || '');
            return loginPattern.test(markerText) && /(登录|sign\s*in|log\s*in|password|密码)/i.test(text + ' ' + markerText);
          });
          const dialogs = [...document.querySelectorAll('[role="dialog"],[aria-modal="true"]')].filter(visible);
          const ordinaryPopupVisible = dialogs.some((dialog) => {
            const text = clean(dialog.innerText || dialog.textContent);
            if (/(captcha|verify|安全验证|人机验证|滑块|验证码|请登录|登录后继续|sign\s*in|log\s*in)/i.test(text)) return false;
            return [...dialog.querySelectorAll('button,[role="button"],[aria-label],[title]')].filter(visible).some((candidate) => {
              const label = clean(candidate.getAttribute('aria-label') || candidate.getAttribute('title') || candidate.innerText || candidate.textContent);
              return /^(×|x|close|关闭|稍后再说|not now)$/i.test(label);
            });
          });
          const bodyText = String(document.body?.innerText || '');
          const accessDeniedVisible = bodyText.length < 5000 && /(access denied|403 forbidden|访问被拒绝|无权访问|请求被拦截)/i.test(bodyText);

          const meta = {};
          for (const selector of [
            ['description', 'meta[name="description"]'],
            ['og:title', 'meta[property="og:title"]'],
            ['og:description', 'meta[property="og:description"]'],
            ['og:url', 'meta[property="og:url"]'],
            ['product:brand', 'meta[property="product:brand"]'],
          ]) {
            const el = document.querySelector(selector[1]);
            if (el && el.content) meta[selector[0]] = clean(el.content);
          }
          return {
            title: clean(document.title),
            visible_text: bodyText,
            table_rows: rows,
            json_ld: jsonLd,
            embedded_data: embedded,
            image_urls: imageUrls.slice(0, 24),
            meta,
            interaction_hints: {
              ready_state: document.readyState,
              challenge_visible: challengeVisible,
              password_visible: passwordVisible,
              login_visible: loginVisible,
              ordinary_popup_visible: ordinaryPopupVisible,
              access_denied_visible: accessDeniedVisible,
            },
          };
        }"""
    )

    visible_text = str(payload.get("visible_text") or "")
    page_state = classify_source_page_state(
        url=str(page.url or ""),
        title=payload.get("title"),
        visible_text=visible_text,
        hints=payload.get("interaction_hints") if isinstance(payload, dict) else {},
    )
    if page_state.state in RECOVERABLE_INTERACTIONS:
        raise SourceInteractionRequired(
            page_state.state,
            page_state.reason,
            observed_url=page_state.observed_url,
            title=page_state.title,
        )
    if page_state.state == ACCESS_DENIED:
        raise SourceAccessBlocked(page_state.reason)

    warnings: list[str] = []
    if len(visible_text) > max_visible_text_chars:
        warnings.append(
            f"visible_text truncated from {len(visible_text)} to {max_visible_text_chars} chars"
        )
        visible_text = visible_text[:max_visible_text_chars]

    embedded_data, embedded_truncated = _bounded_embedded_data(list(payload.get("embedded_data") or []))
    if embedded_truncated:
        warnings.append("embedded_data truncated to 80000 chars")

    rows = [SnapshotTableRow.from_mapping(item) for item in payload.get("table_rows") or []]
    image_urls = []
    seen_urls: set[str] = set()
    for item in payload.get("image_urls") or []:
        value = str(item or "").strip()
        if value and value not in seen_urls:
            seen_urls.add(value)
            image_urls.append(value)

    return SourceSnapshot(
        requested_url=requested_url,
        final_url=str(page.url),
        title=_clean_text(payload.get("title")),
        captured_at=datetime.now(timezone.utc).isoformat(),
        visible_text=visible_text,
        table_rows=rows,
        json_ld=list(payload.get("json_ld") or []),
        embedded_data=embedded_data,
        image_urls=image_urls[:24],
        meta={str(k): _clean_text(v) for k, v in (payload.get("meta") or {}).items()},
        warnings=warnings,
    )
