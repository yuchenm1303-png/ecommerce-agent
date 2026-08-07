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


_BLOCK_PATTERNS = (
    "captcha",
    "verify you are human",
    "security verification",
    "安全验证",
    "人机验证",
    "滑块验证",
    "请完成验证",
)


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
    meta: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
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


def _detect_access_block(text: str) -> str | None:
    lowered = text.casefold()
    for marker in _BLOCK_PATTERNS:
        if marker.casefold() in lowered:
            return marker
    return None


def capture_page_snapshot(
    page: Any,
    *,
    requested_url: str,
    max_visible_text_chars: int = 120_000,
) -> SourceSnapshot:
    """Read an already-loaded browser page without attempting risk-control bypass.

    The caller owns navigation and any legitimate manual login. If the rendered
    page appears to be a CAPTCHA/security-verification screen, capture stops and
    requires manual handling instead of trying to automate around it.
    """

    payload = page.evaluate(
        r"""() => {
          const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
          const visible = (el) => {
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 0 && rect.height > 0;
          };
          const rows = [];
          [...document.querySelectorAll('table')].forEach((table, tableIndex) => {
            [...table.querySelectorAll('tr')].forEach((tr, rowIndex) => {
              if (!visible(tr)) return;
              const cells = [...tr.querySelectorAll('th,td')].map((cell) => clean(cell.innerText || cell.textContent));
              if (cells.length >= 2 && cells[0] && cells.slice(1).join(' ').trim()) {
                rows.push({key: cells[0], value: cells.slice(1).join(' | '), table_index: tableIndex + 1, row_index: rowIndex + 1});
              }
            });
          });

          // Many marketplace parameter panels use div/dl structures instead of
          // semantic tables. Capture explicit dt/dd pairs as table-like facts.
          [...document.querySelectorAll('dl')].forEach((dl, tableIndex) => {
            const dts = [...dl.querySelectorAll(':scope > dt')];
            dts.forEach((dt, rowIndex) => {
              const dd = dt.nextElementSibling;
              if (!dd || dd.tagName.toLowerCase() !== 'dd' || !visible(dt) || !visible(dd)) return;
              const key = clean(dt.innerText || dt.textContent);
              const value = clean(dd.innerText || dd.textContent);
              if (key && value) rows.push({key, value, table_index: 1000 + tableIndex + 1, row_index: rowIndex + 1});
            });
          });

          const jsonLd = [];
          [...document.querySelectorAll('script[type="application/ld+json"]')].forEach((script) => {
            try { jsonLd.push(JSON.parse(script.textContent || '')); } catch (_) {}
          });
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
            visible_text: String(document.body?.innerText || ''),
            table_rows: rows,
            json_ld: jsonLd,
            meta,
          };
        }"""
    )

    visible_text = str(payload.get("visible_text") or "")
    blocked = _detect_access_block(visible_text[:20_000])
    if blocked:
        raise SourceAccessBlocked(
            f"来源页面出现安全/人机验证标记 {blocked!r}；已停止自动采集，请人工完成合法验证后再继续。"
        )

    warnings: list[str] = []
    if len(visible_text) > max_visible_text_chars:
        warnings.append(
            f"visible_text truncated from {len(visible_text)} to {max_visible_text_chars} chars"
        )
        visible_text = visible_text[:max_visible_text_chars]

    rows = [SnapshotTableRow.from_mapping(item) for item in payload.get("table_rows") or []]
    return SourceSnapshot(
        requested_url=requested_url,
        final_url=str(page.url),
        title=_clean_text(payload.get("title")),
        captured_at=datetime.now(timezone.utc).isoformat(),
        visible_text=visible_text,
        table_rows=rows,
        json_ld=list(payload.get("json_ld") or []),
        meta={str(k): _clean_text(v) for k, v in (payload.get("meta") or {}).items()},
        warnings=warnings,
    )
