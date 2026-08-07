"""Makro Seller Center authenticated DOM probe.

Run on the user's own machine after manually logging in to Makro:

    python makro_probe.py --url "<Add a Single Listing URL>"

The probe is intentionally read-only:

* opens an isolated persistent Microsoft Edge profile by default
  (browser_profiles/makro-edge; never committed to git and never touches the
  user's daily Edge profile),
* lets the user log in manually when needed,
* scans the whole page including internal scroll containers,
* recognises native controls and React-style custom dropdowns,
* writes field metadata JSON, a full-page screenshot and a sanitized
  DOM snapshot under logs/makro-probe/,
* never reads password fields, never records field values unless
  --include-values is passed, and never clicks Save / Send to QC.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

from app.platforms.makro import is_makro_listing_page, parse_makro_listing_url


MAKRO_HOME_URL = "https://seller.makro.co.za/"


# ---------------------------------------------------------------------------
# Shared JavaScript helpers (injected into every evaluated script).
# ---------------------------------------------------------------------------

_JS_HELPERS = r"""
const clean = (value) => (value == null ? "" : String(value)).replace(/\s+/g, " ").trim();
const cssEscape = (value) => {
  if (window.CSS && CSS.escape) return CSS.escape(value);
  return value.replace(/([ #;?%&,.+*~\\':"!^$[\]()=>|/@])/g, "\\$1");
};
const escAttr = (value) => String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
const pathOf = (el) => {
  const parts = [];
  let node = el;
  while (node && node.nodeType === 1 && node !== document.documentElement) {
    const tag = node.tagName.toLowerCase();
    const id = node.getAttribute("id");
    if (id) {
      parts.unshift(`${tag}#${cssEscape(id)}`);
    } else {
      let index = 1;
      let sibling = node.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === node.tagName) index += 1;
        sibling = sibling.previousElementSibling;
      }
      parts.unshift(`${tag}:nth-of-type(${index})`);
    }
    node = node.parentElement;
  }
  return parts.join(" > ");
};
const uniqueBy = (list, keyFn) => {
  const seen = new Set();
  const out = [];
  for (const item of list) {
    const key = keyFn(item);
    if (key && !seen.has(key)) {
      seen.add(key);
      out.push(item);
    }
  }
  return out;
};
const isVisible = (el) => {
  if (el.closest && el.closest('[hidden], [aria-hidden="true"]')) return false;
  const style = window.getComputedStyle(el);
  if (style.display === "none" || style.visibility === "hidden") return false;
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
};
const isSensitive = (el) => {
  const type = (el.getAttribute("type") || "").toLowerCase();
  if (type === "password") return true;
  const haystack = [
    el.getAttribute("name") || "",
    el.getAttribute("id") || "",
    el.getAttribute("aria-label") || "",
    el.getAttribute("autocomplete") || "",
  ].join(" ");
  return /password|passwd|token|cookie|session|secret|apikey|api_key|authorization|credential/i.test(haystack);
};
"""

_SCAN_BODY = r"""
  const elements = document.querySelectorAll("*");
  const CONTROL_ROLES = new Set(["combobox", "textbox", "searchbox", "spinbutton", "slider", "checkbox", "radio", "listbox", "option"]);

  const looksLikeControl = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    if (el.getAttribute("contenteditable") === "true") return true;
    const role = (el.getAttribute("role") || "").toLowerCase();
    if (CONTROL_ROLES.has(role)) return true;
    if (el.getAttribute("aria-haspopup") || el.getAttribute("aria-expanded") !== null) return true;
    const testId = (el.getAttribute("data-testid") || "").toLowerCase();
    if (/(dropdown|combobox|autocomplete|select)/.test(testId)) return true;
    const cls = (typeof el.className === "string" ? el.className : "").toLowerCase();
    if (/(dropdown|combobox|autocomplete)/.test(cls)) return true;
    if (/(^|[\s_-])select/.test(cls)) {
      return el.querySelectorAll("[role='option'], option, [role='listbox']").length > 0;
    }
    return false;
  };

  const makroAttributeName = (el) => {
    // Makro renders field names in a sibling name wrapper, e.g.
    // styles__EditAttributeItemWrapper > styles__EditAttributeNameWrapper >
    // styles__AttributeItemLabelName "SKU ID <sup>*</sup>".
    // The common ancestor holding both the label-name wrapper and the field
    // wrapper is EditAttributeItemWrapper. Inner wrappers (e.g.
    // AttributeItemFieldWrapper / AttributeItemFormElementWrapper) do not
    // contain the label, so only match the outer item wrapper.
    const itemWrapper = el.closest('[class*="EditAttributeItemWrapper"]');
    if (!itemWrapper) return null;
    const nameEl = itemWrapper.querySelector('[class*="AttributeItemLabelName"], [class*="AttributeItemLabel"]');
    if (!nameEl) return null;
    const textParts = [];
    nameEl.childNodes.forEach((node) => {
      if (node.nodeType === 3) textParts.push(node.textContent);
    });
    let label = clean(textParts.join(" "));
    if (!label) label = clean((nameEl.textContent || "").replace(/\s*\*+\s*$/, ""));
    const star = nameEl.querySelector(
      '[class*="mandatory-star"], [class*="MandatoryStar"], [class*="mandatoryStar"]'
    );
    return { label, mandatory_star: Boolean(star), wrapper_path: pathOf(itemWrapper) };
  };

  const labelInfo = (el) => {
    const makro = makroAttributeName(el);
    const parts = [];
    const push = (text) => {
      const t = clean(text).replace(/\s*\*+\s*$/, "");
      if (t && !parts.includes(t)) parts.push(t);
    };
    if (makro && makro.label) push(makro.label);
    if (el.id) {
      document.querySelectorAll(`label[for="${cssEscape(el.id)}"]`).forEach((label) => push(label.innerText));
    }
    const wrapping = el.closest("label");
    if (wrapping) push(wrapping.innerText);
    push(clean(el.getAttribute("aria-label")));
    const labelledBy = clean(el.getAttribute("aria-labelledby"));
    if (labelledBy) {
      labelledBy.split(/\s+/).forEach((id) => {
        const node = document.getElementById(id);
        if (node) push(node.innerText || node.textContent);
      });
    }
    const describedBy = clean(el.getAttribute("aria-describedby"));
    const helpText = describedBy
      ? describedBy.split(/\s+/).map((id) => {
          const node = document.getElementById(id);
          return node ? clean(node.innerText || node.textContent) : "";
        }).filter(Boolean).join(" | ")
      : "";
    if (!parts.length) {
      const prev = el.previousElementSibling;
      if (prev) {
        const t = clean(prev.innerText || prev.textContent);
        if (t && t.length <= 120) push(t);
      }
      const placeholder = clean(el.getAttribute("placeholder"));
      if (placeholder) push(placeholder);
    }
    return {
      label: parts.join(" | "),
      help_text: helpText,
      mandatory_star: Boolean(makro && makro.mandatory_star),
    };
  };

  const requiredHint = (el) => {
    if (el.required === true) return "required-attribute";
    const ariaRequired = el.getAttribute("aria-required");
    if (ariaRequired === "true") return "aria-required=true";
    const makro = makroAttributeName(el);
    if (makro && makro.mandatory_star) return "mandatory-star";
    const label = el.id ? document.querySelector(`label[for="${cssEscape(el.id)}"]`) : null;
    const labelEl = label || el.closest("label");
    if (labelEl) {
      const text = clean(labelEl.innerText);
      if (text.includes("*")) return "label-asterisk";
      if (/(必填|必须|required|mandatory)/i.test(text)) return "label-keyword";
    }
    if (el.closest("[data-required], [class*='required' i], [class*='mandatory' i]")) return "container-class";
    return "";
  };

  const findOptionScope = (el) => {
    const role = el.getAttribute("role");
    if (role === "listbox") return el;
    if (role === "option") return el.parentElement || el;
    let node = el;
    for (let depth = 0; node && depth < 6; depth += 1, node = node.parentElement) {
      if (node.querySelectorAll && (node.querySelectorAll("[role='option']").length > 0 || node.querySelectorAll("option").length > 0)) {
        return node;
      }
    }
    return el;
  };

  const optionList = (el) => {
    if (el.tagName.toLowerCase() === "select") {
      return Array.from(el.options || []).map((opt) => ({
        text: clean(opt.textContent),
        value: clean(opt.value),
        selected: opt.selected === true,
        disabled: opt.disabled === true,
      })).filter((opt) => opt.text || opt.value);
    }
    const scope = findOptionScope(el);
    const options = [];
    scope.querySelectorAll("[role='option'], li[data-value], li[data-testid*='option' i], [class*='option' i]").forEach((opt) => {
      if (opt === el) return;
      const text = clean(opt.innerText || opt.textContent);
      const value = clean(opt.getAttribute("data-value")) || clean(opt.getAttribute("value")) || clean(opt.getAttribute("data-key"));
      const selected = opt.getAttribute("aria-selected") === "true" || /(^|\s)selected(\s|$)/i.test(opt.className || "");
      options.push({ text, value, selected, disabled: opt.getAttribute("aria-disabled") === "true" });
    });
    return uniqueBy(options, (opt) => `${opt.text}\u0000${opt.value}`);
  };

  const sectionInfo = (el) => {
    let node = el.parentElement;
    let heading = "";
    let subsection = "";
    let container = "";
    while (node && node.nodeType === 1 && node !== document.body) {
      const cls = typeof node.className === "string" ? node.className : "";
      if (!heading) {
        if (/styles__Card-|Card-sc-/.test(cls)) {
          const titleEl = node.querySelector('[class*="styles__Title-"], [class*="Title-ef7o31"], [class*="Title-"]');
          if (titleEl) heading = clean(titleEl.innerText || titleEl.textContent);
        }
        if (!heading) {
          const headingEl = node.querySelector(":scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > h6, :scope > [role='heading'], :scope > legend");
          if (headingEl) heading = clean(headingEl.innerText || headingEl.textContent);
        }
      }
      if (!subsection && /WebsiteSection/.test(cls)) {
        const nameEl = node.querySelector('[class*="WebsiteSectionName"]');
        if (nameEl) subsection = clean(nameEl.innerText || nameEl.textContent);
      }
      if (!container) {
        const tag = node.tagName.toLowerCase();
        const role = clean(node.getAttribute("role"));
        const testId = clean(node.getAttribute("data-testid"));
        if (/styles__Card-|Card-sc-/.test(cls)) {
          container = `${tag}${node.id ? "#" + cssEscape(node.id) : ""}${cls ? "." + cls.split(/\s+/).slice(0, 3).join(".") : ""}`;
        } else {
          const isSection = tag === "fieldset" || /(section|panel|accordion|card|group|fieldset)/i.test(`${tag} ${cls} ${role} ${testId}`);
          if (isSection) {
            container = `${tag}${node.id ? "#" + cssEscape(node.id) : ""}${cls ? "." + cls.split(/\s+/).slice(0, 3).join(".") : ""}`;
          }
        }
      }
      if (heading && container && subsection) break;
      node = node.parentElement;
    }
    return { section_heading: heading, section_container: container, subsection_heading: subsection };
  };

  const nearestContext = (el, includeValues) => {
    let node = el.parentElement;
    for (let depth = 0; node && depth < 6; depth += 1, node = node.parentElement) {
      const text = includeValues ? clean(node.innerText) : clean(node.textContent);
      if (!text) continue;
      if (text.length >= 8 && text.length <= 260) return text;
    }
    return "";
  };

  const classifyFieldKind = (el, tag, type, role) => {
    if (tag === "select") return "select";
    if (tag === "textarea") return "textarea";
    if (tag === "input") {
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      return "input";
    }
    if (el.getAttribute("contenteditable") === "true") return "contenteditable";
    if (role === "combobox" || el.getAttribute("aria-haspopup")) return "dropdown";
    if (role === "listbox") return "listbox";
    if (role === "option") return "option";
    if (role === "checkbox") return "custom_checkbox";
    if (role === "radio") return "custom_radio";
    if (["textbox", "searchbox", "spinbutton", "slider"].includes(role)) return `custom_${role}`;
    const testId = (el.getAttribute("data-testid") || "").toLowerCase();
    const cls = (typeof el.className === "string" ? el.className : "").toLowerCase();
    if (/(dropdown|combobox)/.test(`${testId} ${cls}`)) return "dropdown";
    if (/autocomplete/.test(`${testId} ${cls}`)) return "autocomplete";
    if (/select/.test(`${testId} ${cls}`)) return "dropdown";
    return "unknown";
  };

  const candidateSelectors = (el) => {
    const selectors = [];
    const push = (s) => { if (s && !selectors.includes(s)) selectors.push(s); };
    const id = el.getAttribute("id");
    const testId = el.getAttribute("data-testid");
    const name = el.getAttribute("name");
    const aria = el.getAttribute("aria-label");
    const placeholder = el.getAttribute("placeholder");
    if (id) push(`#${cssEscape(id)}`);
    if (testId) push(`[data-testid="${escAttr(testId)}"]`);
    if (name) push(`[name="${escAttr(name)}"]`);
    if (aria) push(`[aria-label="${escAttr(aria)}"]`);
    if (placeholder) push(`[placeholder="${escAttr(placeholder)}"]`);
    const role = el.getAttribute("role");
    if (role) push(`[role="${escAttr(role)}"]`);
    if (el.tagName.toLowerCase() === "input") {
      const t = el.getAttribute("type");
      if (t && t !== "text") push(`input[type="${escAttr(t)}"]`);
    }
    push(pathOf(el));
    return selectors;
  };

  const items = [];
  elements.forEach((el) => {
    const tag = el.tagName.toLowerCase();
    const type = clean(el.getAttribute("type") || (tag === "select" ? "select" : ""));
    if (tag === "input" && (type === "password" || type === "hidden")) return;
    if (!looksLikeControl(el)) return;
    if (!isVisible(el)) return;
    const role = clean(el.getAttribute("role"));
    const kind = classifyFieldKind(el, tag, type, role);
    if (kind === "unknown") return;
    const named = labelInfo(el);
    const section = sectionInfo(el);
    const options = optionList(el);
    const reqHint = requiredHint(el);
    const item = {
      ordinal: 0,
      path: pathOf(el),
      field_kind: kind,
      tag,
      type,
      role,
      id: clean(el.getAttribute("id")),
      name: clean(el.getAttribute("name")),
      data_testid: clean(el.getAttribute("data-testid")),
      aria_label: clean(el.getAttribute("aria-label")),
      aria_labelledby: clean(el.getAttribute("aria-labelledby")),
      placeholder: clean(el.getAttribute("placeholder")),
      autocomplete: clean(el.getAttribute("autocomplete")),
      inputmode: clean(el.getAttribute("inputmode")),
      tabindex: el.getAttribute("tabindex"),
      maxlength: el.getAttribute("maxlength") ? Number(el.getAttribute("maxlength")) : null,
      min: el.getAttribute("min"),
      max: el.getAttribute("max"),
      step: el.getAttribute("step"),
      pattern: el.getAttribute("pattern"),
      readonly: el.readOnly === true || el.hasAttribute("readonly") || el.getAttribute("aria-readonly") === "true",
      disabled: el.disabled === true || el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true",
      required: Boolean(reqHint),
      required_hint: reqHint,
      label: named.label,
      help_text: named.help_text,
      context_text: nearestContext(el, includeValues),
      section_heading: section.section_heading,
      section_container: section.section_container,
      subsection_heading: section.subsection_heading,
      has_dropdown_options: options.length > 0,
      options,
      selector_candidates: candidateSelectors(el),
      value_recorded: false,
    };
    if (includeValues && !isSensitive(el)) {
      const value = clean(el.value);
      if (value) {
        item.value = value;
        item.value_recorded = true;
      }
    }
    items.push(item);
  });
  return items;
"""


_SCAN_SCRIPT = "(includeValues) => {\n" + _JS_HELPERS + _SCAN_BODY + "\n}"

_FIND_SCROLL_CONTAINERS_SCRIPT = (
    "() => {\n"
    + _JS_HELPERS
    + r"""
  const containers = [];
  document.querySelectorAll("*").forEach((el) => {
    if (el === document.documentElement || el === document.body) return;
    const style = window.getComputedStyle(el);
    const oy = style.overflowY;
    const ox = style.overflowX;
    const scrollableY = (oy === "auto" || oy === "scroll" || oy === "overlay") && el.scrollHeight > el.clientHeight + 2;
    const scrollableX = (ox === "auto" || ox === "scroll" || ox === "overlay") && el.scrollWidth > el.clientWidth + 2;
    if (scrollableY || scrollableX) {
      containers.push({
        path: pathOf(el),
        overflow_y: oy,
        overflow_x: ox,
        scroll_height: el.scrollHeight,
        client_height: el.clientHeight,
        scroll_width: el.scrollWidth,
        client_width: el.clientWidth,
      });
    }
  });
  return containers;
"""
    + "\n}"
)

_SCROLL_WINDOW_SCRIPT = (
    "() => {\n"
    + r"""
  const doc = document.scrollingElement || document.documentElement;
  const beforeY = doc.scrollTop;
  const beforeX = doc.scrollLeft;
  doc.scrollTop = beforeY + Math.max(1, Math.floor(window.innerHeight * 0.8));
  doc.scrollLeft = beforeX + Math.max(1, Math.floor(window.innerWidth * 0.8));
  return {
    moved: doc.scrollTop !== beforeY || doc.scrollLeft !== beforeX,
    at_bottom: doc.scrollTop + window.innerHeight >= doc.scrollHeight - 2,
    at_right: doc.scrollLeft + window.innerWidth >= doc.scrollWidth - 2,
    scroll_height: doc.scrollHeight,
    scroll_width: doc.scrollWidth,
  };
"""
    + "\n}"
)

_SCROLL_CONTAINER_SCRIPT = (
    "({path}) => {\n"
    + r"""
  const el = document.querySelector(path);
  if (!el) return { moved: false, at_bottom: true, at_right: true };
  const beforeY = el.scrollTop;
  const beforeX = el.scrollLeft;
  if (el.scrollHeight > el.clientHeight + 2) {
    el.scrollTop = beforeY + Math.max(1, Math.floor(el.clientHeight * 0.8));
  }
  if (el.scrollWidth > el.clientWidth + 2) {
    el.scrollLeft = beforeX + Math.max(1, Math.floor(el.clientWidth * 0.8));
  }
  return {
    moved: el.scrollTop !== beforeY || el.scrollLeft !== beforeX,
    at_bottom: el.scrollTop + el.clientHeight >= el.scrollHeight - 2,
    at_right: el.scrollLeft + el.clientWidth >= el.scrollWidth - 2,
    scroll_height: el.scrollHeight,
    scroll_width: el.scrollWidth,
  };
"""
    + "\n}"
)

_SCROLL_TO_END_SCRIPT = (
    "({path}) => {\n"
    + r"""
  const el = document.querySelector(path);
  if (!el) return { moved: false };
  const beforeY = el.scrollTop;
  const beforeX = el.scrollLeft;
  el.scrollTop = el.scrollHeight;
  el.scrollLeft = el.scrollWidth;
  return { moved: el.scrollTop !== beforeY || el.scrollLeft !== beforeX };
"""
    + "\n}"
)

_CLICK_BY_PATH_SCRIPT = (
    "({path}) => {\n"
    + r"""
  const el = document.querySelector(path);
  if (!el) return false;
  try {
    el.scrollIntoView({ block: "center", inline: "center" });
    el.click();
    return true;
  } catch (err) {
    return false;
  }
"""
    + "\n}"
)

_READ_OPEN_OPTIONS_SCRIPT = (
    "() => {\n"
    + _JS_HELPERS
    + r"""
  const items = [];
  document.querySelectorAll("[role='option'], li[data-value], [class*='option' i]").forEach((opt) => {
    if (!isVisible(opt)) return;
    const text = clean(opt.innerText || opt.textContent);
    if (!text) return;
    items.push({
      text,
      value: clean(opt.getAttribute("data-value")) || clean(opt.getAttribute("value")) || clean(opt.getAttribute("data-key")),
      selected: opt.getAttribute("aria-selected") === "true" || /(^|\s)selected(\s|$)/i.test(opt.className || ""),
    });
  });
  return uniqueBy(items, (opt) => `${opt.text}\u0000${opt.value}`);
"""
    + "\n}"
)

_FIND_SECTIONS_SCRIPT = (
    "() => {\n"
    + _JS_HELPERS
    + r"""
  const cards = [];
  document.querySelectorAll('[class*="styles__Card-"], [class*="Card-sc-"], [data-testid*="card" i]').forEach((card) => {
    if (!isVisible(card)) return;
    const titleEl = card.querySelector('[class*="styles__Title-"], [class*="Title-ef7o31"], [class*="Title-"]');
    const title = titleEl ? clean(titleEl.innerText || titleEl.textContent) : "";
    if (!title) return;
    const buttons = Array.from(card.querySelectorAll("button"));
    const links = Array.from(card.querySelectorAll("a"));
    const editBtn = buttons.find((b) => clean(b.innerText).toUpperCase() === "EDIT");
    const saveBtn = buttons.find((b) => clean(b.innerText).toUpperCase() === "SAVE");
    const cancelEl = [...links, ...buttons].find((b) => clean(b.innerText).toUpperCase() === "CANCEL");
    const hasFields = card.querySelectorAll('input, textarea, select, [role="combobox"], [contenteditable="true"]').length > 0;
    const imageCount = card.querySelectorAll('img').length;
    cards.push({
      path: pathOf(card),
      title,
      expanded: !editBtn,
      has_edit: Boolean(editBtn),
      has_cancel: Boolean(cancelEl),
      has_save: Boolean(saveBtn),
      has_fields: hasFields,
      image_count: imageCount,
    });
  });
  return cards;
"""
    + "\n}"
)

_CLICK_EDIT_SCRIPT = (
    "({path}) => {\n"
    + _JS_HELPERS
    + r"""
  const card = document.querySelector(path);
  if (!card) return false;
  const btn = Array.from(card.querySelectorAll("button")).find((b) => clean(b.innerText).toUpperCase() === "EDIT");
  if (!btn) return false;
  try {
    btn.scrollIntoView({ block: "center", inline: "center" });
    btn.click();
    return true;
  } catch (err) {
    return false;
  }
"""
    + "\n}"
)

_CLICK_CANCEL_SCRIPT = (
    "({path}) => {\n"
    + _JS_HELPERS
    + r"""
  const card = document.querySelector(path);
  if (!card) return false;
  const el = [...card.querySelectorAll("a, button")].find((b) => clean(b.innerText).toUpperCase() === "CANCEL");
  if (!el) return false;
  try {
    el.scrollIntoView({ block: "center", inline: "center" });
    el.click();
    return true;
  } catch (err) {
    return false;
  }
"""
    + "\n}"
)

_SECTION_STATE_SCRIPT = (
    "({path}) => {\n"
    + _JS_HELPERS
    + r"""
  const card = document.querySelector(path);
  if (!card) return { found: false };
  const buttons = Array.from(card.querySelectorAll("button"));
  const links = Array.from(card.querySelectorAll("a"));
  const editBtn = buttons.find((b) => clean(b.innerText).toUpperCase() === "EDIT");
  const cancelEl = [...links, ...buttons].find((b) => clean(b.innerText).toUpperCase() === "CANCEL");
  const hasFields = card.querySelectorAll(
    'input, textarea, select, [role="combobox"], [contenteditable="true"]'
  ).length > 0;
  return {
    found: true,
    has_edit: Boolean(editBtn),
    has_cancel: Boolean(cancelEl),
    has_fields: hasFields,
  };
"""
    + "\n}"
)


# ---------------------------------------------------------------------------
# Pure helpers (testable without a browser).
# ---------------------------------------------------------------------------

_RICHNESS_KEYS = (
    "label",
    "options",
    "section_heading",
    "context_text",
    "help_text",
    "placeholder",
    "aria_label",
    "data_testid",
)


def _richness(item: dict[str, Any]) -> int:
    score = sum(1 for key in _RICHNESS_KEYS if item.get(key))
    score += min(len(item.get("options") or []), 50)
    return score


def merge_scans(scans: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge repeated scans, keeping the richest entry per stable DOM path."""

    best: dict[str, dict[str, Any]] = {}
    for scan in scans:
        for item in scan:
            path = item.get("path")
            if not path:
                continue
            current = best.get(path)
            if current is None or _richness(item) > _richness(current):
                best[path] = item
    controls = sorted(best.values(), key=lambda item: item.get("path", ""))
    for ordinal, item in enumerate(controls):
        item["ordinal"] = ordinal
    return controls


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

# ---------------------------------------------------------------------------
# Browser-side probe orchestration.
# ---------------------------------------------------------------------------

def build_launch_kwargs(
    *,
    browser: str,
    profile_dir: Path,
    headless: bool,
) -> dict[str, Any]:
    """Build persistent-context launch kwargs.

    Edge uses Playwright's msedge channel with an isolated user-data-dir so the
    user's daily Edge profile is never opened or modified.
    """

    kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "viewport": {"width": 1600, "height": 1000},
    }
    if browser == "edge":
        kwargs["channel"] = "msedge"
    return kwargs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在本机已授权登录后，采集 Makro Add Listing 页面字段元数据。"
    )
    parser.add_argument(
        "--url",
        default=None,
        help="可选。Add a Single Listing 完整 URL；仅作为初始导航/校验使用，"
        "按 Enter 后程序直接采集当前页面，不会强制跳回旧 URL（requestId 可能已失效）",
    )
    parser.add_argument(
        "--browser",
        choices=("edge", "chromium"),
        default="edge",
        help="默认使用本机 Microsoft Edge（channel=msedge）和独立 persistent profile；"
        "chromium 用于调试（需要先 playwright install chromium）",
    )
    parser.add_argument(
        "--profile-dir",
        default="browser_profiles/makro-edge",
        help="本地持久化浏览器目录；Edge 默认使用独立目录，不会接管日常 Edge",
    )
    parser.add_argument(
        "--output-dir",
        default="logs/makro-probe",
        help="字段快照、DOM 快照和截图输出目录",
    )
    parser.add_argument(
        "--include-values",
        action="store_true",
        help="调试时包含当前输入值；默认关闭以减少敏感数据落盘",
    )
    parser.add_argument(
        "--no-dom-snapshot",
        action="store_true",
        help="不生成 makro-dom-*.html 安全快照",
    )
    parser.add_argument(
        "--open-dropdowns",
        action="store_true",
        help="尝试点击自定义下拉框读取弹出选项后关闭（可能有轻微副作用，默认关闭）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头模式；仅在 profile 已登录且页面可复用时使用",
    )
    parser.add_argument(
        "--scan-sections",
        action="store_true",
        help="扫描所有 listing section：点击 EDIT 展开 Product Description / "
        "Additional Description / Product Photos 后逐 section 滚动扫描；"
        "只点安全 Cancel，不填写、不保存、不上传、不点 Send to QC",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="保持同一个 Edge 会话：单次登录后可反复扫描多个 Add Listing 页面；"
        "每次扫描后询问是否继续，结束时询问是否保持浏览器打开"
        "（默认询问，Y 保持；不记录任何认证数据）",
    )
    parser.add_argument(
        "--scroll-wait-ms",
        type=int,
        default=350,
        help="每次滚动后等待懒加载的时间（毫秒）",
    )
    parser.add_argument(
        "--max-scroll-steps",
        type=int,
        default=200,
        help="单个滚动容器最大滚动次数（安全上限）",
    )
    return parser


def capture_controls(page: Page, include_values: bool = False) -> list[dict[str, Any]]:
    """Scan the whole DOM and return metadata for every recognised control.

    Password/hidden inputs are excluded. Field values are only recorded when
    include_values is True and the control is not considered sensitive.
    """

    return page.evaluate(_SCAN_SCRIPT, include_values)


def find_scroll_containers(page: Page) -> list[dict[str, Any]]:
    return page.evaluate(_FIND_SCROLL_CONTAINERS_SCRIPT)


def scroll_window(page: Page) -> dict[str, Any]:
    return page.evaluate(_SCROLL_WINDOW_SCRIPT)


def scroll_container(page: Page, path: str) -> dict[str, Any]:
    return page.evaluate(_SCROLL_CONTAINER_SCRIPT, {"path": path})


def scroll_to_end(page: Page, path: str) -> dict[str, Any]:
    return page.evaluate(_SCROLL_TO_END_SCRIPT, {"path": path})


def capture_dropdown_options(
    page: Page,
    controls: list[dict[str, Any]],
    *,
    wait_ms: int = 350,
) -> tuple[list[dict[str, Any]], int]:
    """Optionally open custom dropdowns to read options, then close them."""

    opened = 0
    for item in controls:
        if item.get("field_kind") not in {"dropdown", "autocomplete", "listbox", "select"}:
            continue
        if item.get("has_dropdown_options"):
            continue
        path = item.get("path")
        if not path:
            continue
        clicked = page.evaluate(_CLICK_BY_PATH_SCRIPT, {"path": path})
        if not clicked:
            continue
        page.wait_for_timeout(wait_ms)
        options = page.evaluate(_READ_OPEN_OPTIONS_SCRIPT)
        if options:
            item["options"] = options
            item["has_dropdown_options"] = True
            item["options_captured_via_interaction"] = True
            opened += 1
        page.keyboard.press("Escape")
        page.wait_for_timeout(120)
    return controls, opened

def scroll_and_capture(
    page: Page,
    *,
    include_values: bool = False,
    open_dropdowns: bool = False,
    wait_ms: int = 350,
    max_scroll_steps: int = 200,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scan the page while scrolling the window and every internal container."""

    scans: list[list[dict[str, Any]]] = [capture_controls(page, include_values=include_values)]
    stats: dict[str, Any] = {
        "scroll_containers_found": 0,
        "scroll_passes": 1,
        "dropdowns_opened": 0,
    }

    for _ in range(max_scroll_steps):
        state = scroll_window(page)
        if not state.get("moved"):
            break
        page.wait_for_timeout(wait_ms)
        scans.append(capture_controls(page, include_values=include_values))
        stats["scroll_passes"] += 1

    containers = find_scroll_containers(page)
    stats["scroll_containers_found"] = len(containers)
    for container in containers:
        path = container.get("path")
        if not path:
            continue
        for _ in range(max_scroll_steps):
            state = scroll_container(page, path)
            if not state.get("moved"):
                break
            page.wait_for_timeout(wait_ms)
            scans.append(capture_controls(page, include_values=include_values))
            stats["scroll_passes"] += 1
        end_state = scroll_to_end(page, path)
        if end_state.get("moved"):
            page.wait_for_timeout(wait_ms)
            scans.append(capture_controls(page, include_values=include_values))
            stats["scroll_passes"] += 1

    controls = merge_scans(scans)

    if open_dropdowns:
        controls, opened = capture_dropdown_options(page, controls, wait_ms=wait_ms)
        stats["dropdowns_opened"] = opened

    return controls, stats


_INDEXED_NAME_RE = re.compile(r"_\d+_(?:value|qualifier|display|unit|name)?$")
_VALUE_IDX_RE = re.compile(r"_(\d+)_value$")


def derive_attribute_key(control: dict[str, Any]) -> str:
    """Stable Makro attribute key: id first, then indexed-name stripping.

    Multi-value controls share the attribute id (e.g. all keywords slots have
    id=keywords with names keywords_0_value .. keywords_4_value). Controls
    without an id (e.g. qualifier selects) fall back to stripping the repeated
    index from the name. Labels are only a last-resort fallback.
    """
    cid = control.get("id")
    if cid:
        return cid
    name = control.get("name") or ""
    key = _INDEXED_NAME_RE.sub("", name)
    if key:
        return key
    label = control.get("label") or ""
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if key:
        return key
    return control.get("path") or "unknown"


def _first_nonempty(values) -> str:
    for value in values:
        if value:
            return value
    return ""


def _merge_semantic_field(key: str, controls: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate all DOM controls of one Makro attribute into one semantic field."""
    label = _first_nonempty(c.get("label") for c in controls)
    section_heading = _first_nonempty(c.get("section_heading") for c in controls)
    subsection_heading = _first_nonempty(c.get("subsection_heading") for c in controls)
    required = any(c.get("required") for c in controls)
    required_hint = (
        _first_nonempty(c.get("required_hint") for c in controls if c.get("required"))
        if required
        else ""
    )
    help_text = _first_nonempty(c.get("help_text") for c in controls)
    kinds = [c.get("field_kind") for c in controls]
    main_kind = kinds[0] if kinds else ""
    # The control whose id equals the attribute key is the primary value input.
    for control in controls:
        if control.get("id") == key and control.get("field_kind"):
            main_kind = control["field_kind"]
            break
    options: list[dict[str, Any]] = []
    seen_options: set[tuple[str, str]] = set()
    for control in controls:
        for option in control.get("options") or []:
            dedupe = (str(option.get("text") or ""), str(option.get("value") or ""))
            if dedupe not in seen_options:
                seen_options.add(dedupe)
                options.append(option)
    value_indices = {
        int(match.group(1))
        for control in controls
        if (match := _VALUE_IDX_RE.search(control.get("name") or ""))
    }
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section_heading,
        "subsection_heading": subsection_heading,
        "required": required,
        "required_hint": required_hint,
        "help_text": help_text,
        "field_kind": main_kind,
        "accepted_control_kinds": sorted({kind for kind in kinds if kind}),
        "options": options,
        "multi_value": len(value_indices) > 1,
        "controls": controls,
    }


def build_semantic_fields(controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group DOM controls into semantic Makro attributes.

    One multi-value attribute (one id, names like x_0_value / x_1_value) always
    produces exactly one semantic field containing all its controls. Fields are
    bucketed by (section_heading, attribute_key) so the same attribute id in
    different sections (e.g. "Height" in dimensions vs product attributes) stays
    separate. Nothing is hardcoded: keys come from the DOM, labels are only a
    fallback.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for control in controls:
        key = derive_attribute_key(control)
        bucket = (control.get("section_heading") or "", key)
        if bucket not in groups:
            groups[bucket] = []
            order.append(bucket)
        groups[bucket].append(control)
    fields = [_merge_semantic_field(bucket[1], groups[bucket]) for bucket in order]
    fields.sort(
        key=lambda field: (
            field.get("section_heading") or "",
            field.get("subsection_heading") or "",
            field.get("label") or "",
            field.get("attribute_key") or "",
        )
    )
    return fields


def find_sections(page: Page) -> list[dict[str, Any]]:
    """List all listing section cards with title and expanded state."""
    return page.evaluate(_FIND_SECTIONS_SCRIPT)


def scan_section_fields(
    page: Page,
    section_path: str,
    *,
    include_values: bool = False,
    wait_ms: int = 350,
    max_scroll_steps: int = 200,
) -> list[dict[str, Any]]:
    """Scroll the window plus the section's own containers and scan its fields."""

    scans: list[list[dict[str, Any]]] = [
        capture_controls(page, include_values=include_values)
    ]
    for _ in range(max_scroll_steps):
        state = scroll_window(page)
        if not state.get("moved"):
            break
        page.wait_for_timeout(wait_ms)
        scans.append(capture_controls(page, include_values=include_values))

    for container in find_scroll_containers(page):
        container_path = container.get("path", "")
        if not container_path.startswith(section_path + " > "):
            continue
        for _ in range(max_scroll_steps):
            state = scroll_container(page, container_path)
            if not state.get("moved"):
                break
            page.wait_for_timeout(wait_ms)
            scans.append(capture_controls(page, include_values=include_values))

    merged = merge_scans(scans)
    prefix = section_path + " > "
    return [item for item in merged if item.get("path", "").startswith(prefix)]


def scan_sections(
    page: Page,
    *,
    include_values: bool = False,
    wait_ms: int = 350,
    max_scroll_steps: int = 200,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Expand every listing section that has an EDIT button, scan it, collapse.

    Unified safe strategy for all sections (Price, Stock and Shipping
    Information / Product Description / Additional Description / Product
    Photos): click EDIT, wait for fields to render, scan that section alone,
    then click only the safe Cancel. Never fills values, never uploads files,
    never clicks Save or Send to QC.
    """

    sections = find_sections(page)
    stats: dict[str, Any] = {
        "sections_found": len(sections),
        "sections_expanded_by_scan": 0,
        "sections_cancelled": 0,
    }
    section_results: list[dict[str, Any]] = []
    flat_scans: list[list[dict[str, Any]]] = []

    for section in sections:
        title = section.get("title", "")
        section_path = section.get("path")
        if not section_path:
            continue

        # Re-check the live state: opening one section can collapse another.
        state = page.evaluate(_SECTION_STATE_SCRIPT, {"path": section_path})
        was_collapsed = bool(state.get("has_edit"))
        expanded = not was_collapsed
        if was_collapsed:
            clicked = page.evaluate(_CLICK_EDIT_SCRIPT, {"path": section_path})
            if clicked:
                stats["sections_expanded_by_scan"] += 1
                expanded = True
                _wait_for_section_fields(
                    page, section_path, wait_ms=wait_ms, timeout_s=10.0
                )

        controls = scan_section_fields(
            page,
            section_path,
            include_values=include_values,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        for item in controls:
            if not item.get("section_heading"):
                item["section_heading"] = title

        semantic_fields = build_semantic_fields(controls)
        section_results.append(
            {
                "title": title,
                "expanded": expanded,
                "image_count": section.get("image_count"),
                "field_count": sum(
                    1 for item in controls if item.get("field_kind") != "option"
                ),
                "semantic_field_count": len(semantic_fields),
                "semantic_fields": semantic_fields,
                "controls": controls,
            }
        )
        flat_scans.append(controls)

        if was_collapsed:
            cancelled = page.evaluate(_CLICK_CANCEL_SCRIPT, {"path": section_path})
            if cancelled:
                stats["sections_cancelled"] += 1
                page.wait_for_timeout(wait_ms)

    flat_controls = merge_scans(flat_scans)
    return section_results, flat_controls, stats


def _wait_for_section_fields(
    page: Page, section_path: str, *, wait_ms: int, timeout_s: float
) -> bool:
    """Poll until the section renders fields or shows the Cancel control.

    Field-less sections (e.g. Product Photos) only show Cancel once the EDIT
    click has taken effect; waiting for either signal avoids a fixed long sleep.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = page.evaluate(_SECTION_STATE_SCRIPT, {"path": section_path})
        if state.get("has_fields") or state.get("has_cancel"):
            return True
        page.wait_for_timeout(int(wait_ms))
    return False


def _wait_for_listing_page(page: Page, timeout_s: int, poll_s: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if is_makro_listing_page(page):
            return True
        page.wait_for_timeout(int(poll_s * 1000))
    return False


def _is_listing_url(url: str) -> bool:
    """Return True when the URL looks like a valid Makro single-listing URL."""
    try:
        parse_makro_listing_url(url)
        return True
    except ValueError:
        return False


def wait_for_authenticated_listing(
    page: Page,
    initial_url: str | None = None,
    *,
    headless: bool = False,
    timeout_s: int = 30,
    navigate_first: bool = True,
) -> None:
    """Ensure the current page is an authenticated Add a Single Listing page.

    First call (navigate_first=True): open Makro, detect whether a previous
    login in the persistent profile is still valid, and let the user log in /
    navigate manually. Repeat calls (navigate_first=False) reuse the same Edge
    session: the user opens a new Add Listing page and we only verify the
    current page. We never force page.goto() back to stale requestId URLs.
    """

    if navigate_first:
        if initial_url is None:
            initial_url = MAKRO_HOME_URL

        page.goto(initial_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

        # A validated listing URL that already renders markers needs no prompt.
        if _is_listing_url(initial_url) and _wait_for_listing_page(page, timeout_s=timeout_s):
            return

        if headless:
            raise RuntimeError(
                "无头模式无法手动登录。请去掉 --headless，在自动化 Edge 窗口中"
                "手动登录并进入 Add a Single Listing。"
            )

        if _is_logged_in(page):
            print("\n检测到 Makro 登录状态仍有效（复用现有登录，无需重新登录）。")
            print("请在这个自动化浏览器窗口中从页面正常进入")
            print("Add a Single Listing（保持在该页面）；")
        else:
            print("\n已打开 Makro 首页（自动化浏览器，独立 profile）。")
            print("请在这个浏览器窗口中：")
            print("  1. 手动登录 Makro；")
            print("  2. 从页面正常进入 Add a Single Listing（保持在该页面）；")
        print("完成后回到终端按 Enter，程序将直接采集当前页面。")
        input()
    else:
        print("\n请在这个已打开的自动化浏览器窗口中进入新的")
        print("Add a Single Listing 页面（保持在该页面）；")
        print("完成后回到终端按 Enter，程序将直接采集当前页面。")
        input()

    page.wait_for_timeout(1200)
    # Check the CURRENT page only; never jump back to a stale requestId URL.
    if _wait_for_listing_page(page, timeout_s=min(timeout_s, 10)):
        return

    raise RuntimeError(
        "当前页面不是 Add a Single Listing，已停止采集。\n"
        "请确认：已登录，且自动化浏览器的当前标签页停留在\n"
        "Add a Single Listing 页面。\n"
        "程序不会自动跳转旧 requestId URL。"
    )


def _is_makro_host(url: str) -> bool:
    """Return True when the URL points at the Makro seller host."""
    try:
        return urlparse(url).hostname == "seller.makro.co.za"
    except ValueError:
        return False


def _is_logged_in(page: Page) -> bool:
    """Best-effort login-state detection for skipping the manual-login prompt.

    Heuristic only; never reads or records cookies, tokens, sessionStorage or
    Authorization data, and is not an auth bypass: every scan still requires
    the Add Listing markers via is_makro_listing_page(). A wrong guess is
    recoverable because the user can still log in before pressing Enter.
    """

    if not _is_makro_host(page.url):
        return False
    if page.locator('input[type="password"]').count() > 0:
        return False
    try:
        text = page.evaluate(
            "() => (document.body ? (document.body.innerText || '').slice(0, 30000) : '')"
        )
    except Exception:
        return False
    lower = (text or "").lower()
    if not lower.strip():
        return False
    if any(marker in lower for marker in ("sign out", "log out", "logout", "my account")):
        return True
    if any(
        marker in lower
        for marker in ("sign in", "log in", "login", "password", "forgot password", "welcome back")
    ):
        return False
    return True


def _ask_yes_no(prompt: str, *, default: bool = True) -> bool:
    """Ask a [Y/n] or [y/N] question and return the boolean answer."""
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{prompt}{suffix} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in {"y", "yes"}


def _profile_artifacts(profile_dir: Path) -> str:
    """Short confirmation that a Chromium persistent profile exists at path."""
    markers = [
        name
        for name in ("Local State", "Default", "Preferences")
        if (profile_dir / name).exists()
    ]
    return f"检测到 {', '.join(markers)}" if markers else "目录已创建（等待浏览器写入）"


def capture_listing(
    page: Page,
    *,
    output_dir: Path,
    stamp: str,
    include_values: bool,
    open_dropdowns: bool,
    scan_sections_mode: bool,
    no_dom_snapshot: bool,
    scroll_wait_ms: int,
    max_scroll_steps: int,
) -> dict[str, Any]:
    """Scan the current listing page and write JSON / screenshot / DOM snapshot.

    Read-only: never fills values, never uploads files and never clicks
    Save / Send to QC. Returns the JSON payload.
    """

    if scan_sections_mode:
        sections_payload, controls, section_stats = scan_sections(
            page,
            include_values=include_values,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        scan_stats: dict[str, Any] = {"sections_scan": section_stats}
    else:
        controls, scan_stats = scroll_and_capture(
            page,
            include_values=include_values,
            open_dropdowns=open_dropdowns,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        sections_payload = None

    json_path = output_dir / f"makro-fields-{stamp}.json"
    screenshot_path = output_dir / f"makro-page-{stamp}.png"
    dom_path = output_dir / f"makro-dom-{stamp}.html"

    dom_snapshot_saved = False
    if not no_dom_snapshot:
        sanitized = sanitize_dom_snapshot(page.content())
        dom_path.write_text(sanitized, encoding="utf-8")
        dom_snapshot_saved = True

    try:
        current_target = parse_makro_listing_url(page.url)
    except ValueError:
        current_target = None

    semantic_fields = build_semantic_fields(controls)
    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "platform": "makro",
        "host": "seller.makro.co.za",
        "brand": current_target.brand if current_target else None,
        "vertical": current_target.vertical if current_target else None,
        "request_id_present": bool(current_target and current_target.request_id),
        "vid": current_target.vid if current_target else None,
        "page_url": page.url,
        "include_values": bool(include_values),
        "open_dropdowns": bool(open_dropdowns),
        "scan": scan_stats,
        "sections": sections_payload,
        "dom_snapshot_saved": dom_snapshot_saved,
        "control_count": len(controls),
        "field_count": sum(
            1 for item in controls if item.get("field_kind") != "option"
        ),
        "semantic_field_count": len(semantic_fields),
        "semantic_fields": semantic_fields,
        "controls": controls,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page.screenshot(path=str(screenshot_path), full_page=True)
    return payload


def main() -> int:
    args = build_parser().parse_args()
    target = None
    initial_url = MAKRO_HOME_URL
    if args.url:
        try:
            target = parse_makro_listing_url(args.url)
            initial_url = target.url
        except ValueError:
            initial_url = args.url
            print(f"提示：--url 不是 Add a Single Listing 格式，仅作为初始导航地址：{initial_url}")
    profile_dir = Path(args.profile_dir)
    output_dir = Path(args.output_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    browser_label = "Microsoft Edge" if args.browser == "edge" else "Chromium"
    print(f"浏览器：{browser_label}")
    print(f"user_data_dir：{profile_dir.resolve()}")
    print(f"profile 确认：{_profile_artifacts(profile_dir)}（始终复用同一持久化目录）")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            **build_launch_kwargs(
                browser=args.browser,
                profile_dir=profile_dir,
                headless=args.headless,
            )
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(15_000)

        try:
            iteration = 0
            while True:
                iteration += 1
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                if iteration > 1:
                    stamp = f"{stamp}-{iteration:02d}"
                json_path = output_dir / f"makro-fields-{stamp}.json"
                screenshot_path = output_dir / f"makro-page-{stamp}.png"
                dom_path = output_dir / f"makro-dom-{stamp}.html"

                if iteration > 1:
                    print(f"\n===== 第 {iteration} 个页面 =====")
                wait_for_authenticated_listing(
                    page,
                    initial_url,
                    headless=args.headless,
                    navigate_first=iteration == 1,
                )

                payload = capture_listing(
                    page,
                    output_dir=output_dir,
                    stamp=stamp,
                    include_values=args.include_values,
                    open_dropdowns=args.open_dropdowns,
                    scan_sections_mode=args.scan_sections,
                    no_dom_snapshot=args.no_dom_snapshot,
                    scroll_wait_ms=args.scroll_wait_ms,
                    max_scroll_steps=args.max_scroll_steps,
                )

                print(
                    f"已采集 {payload['control_count']} 个控件，"
                    f"聚合为 {payload['semantic_field_count']} 个语义字段"
                    f"（DOM 字段 {payload['field_count']} 个）。"
                )
                print(f"字段元数据：{json_path}")
                print(f"页面截图：{screenshot_path}")
                if payload.get("dom_snapshot_saved"):
                    print(f"安全 DOM 快照：{dom_path}")
                if args.scan_sections:
                    sec = payload["scan"]["sections_scan"]
                    print(
                        f"section 扫描：发现 {sec['sections_found']} 个 section，"
                        f"展开 {sec['sections_expanded_by_scan']} 个，"
                        f"Cancel {sec['sections_cancelled']} 个。"
                    )
                else:
                    print(
                        f"滚动扫描：{payload['scan']['scroll_passes']} 次扫描，"
                        f"{payload['scan']['scroll_containers_found']} 个内部滚动容器。"
                    )

                if not args.keep_open:
                    break
                if not _ask_yes_no("继续扫描下一个页面？ ", default=True):
                    break

            if args.keep_open:
                if _ask_yes_no("继续保持 Edge 打开吗？ ", default=True):
                    print("Edge 保持打开（context 未关闭）。处理完后回终端按 Enter 关闭。")
                    try:
                        input()
                    except EOFError:
                        pass
        finally:
            context.close()

    print("默认没有记录密码/隐藏字段，也没有点击 Save / Send to QC。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())