"""Sanitized DOM snapshot writing for the Makro probe (read-only)."""

from __future__ import annotations

import re
from html.parser import HTMLParser

_SENSITIVE_ATTR_NAME_RE = re.compile(
    r"token|cookie|session|secret|credential|authorization|apikey|api_key|password|passwd|bearer",
    re.IGNORECASE,
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{1,}){1,3}\b")


class _SanitizingHTMLParser(HTMLParser):
    """Rebuild HTML without script contents, input values or sensitive attrs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._parts: list[str] = []
        self._script_depth = 0
        self._textarea_open = False

    @staticmethod
    def _is_sensitive_attr(name: str) -> bool:
        return bool(_SENSITIVE_ATTR_NAME_RE.search(name))

    def _emit_start(self, tag: str, attrs: list[tuple[str, str | None]], self_closing: bool) -> None:
        if tag == "script":
            self._script_depth += 1
            return
        filtered: list[str] = []
        for name, value in attrs:
            if self._is_sensitive_attr(name):
                continue
            if tag in {"input", "select"} and name.lower() == "value":
                continue
            if value is None:
                filtered.append(f" {name}")
            elif value and _SENSITIVE_ATTR_NAME_RE.search(value):
                filtered.append(f' {name}="[REDACTED]"')
            else:
                filtered.append(f' {name}="{value}"')
        suffix = " />" if self_closing else ">"
        self._parts.append(f"<{tag}{''.join(filtered)}{suffix}")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "textarea":
            self._textarea_open = True
        self._emit_start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "textarea":
            self._textarea_open = True
        self._emit_start(tag, attrs, self_closing=True)
        if tag == "textarea":
            self._textarea_open = False

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._script_depth = max(0, self._script_depth - 1)
            return
        if tag == "textarea":
            self._textarea_open = False
        self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._script_depth or self._textarea_open:
            return
        self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self._script_depth and not self._textarea_open:
            self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._script_depth and not self._textarea_open:
            self._parts.append(f"&#{name};")

def sanitize_dom_snapshot(html: str) -> str:
    """Strip values and secrets for a safe offline DOM snapshot."""

    parser = _SanitizingHTMLParser()
    parser.feed(html)
    parser.close()
    return _JWT_RE.sub("[REDACTED]", "".join(parser._parts))

