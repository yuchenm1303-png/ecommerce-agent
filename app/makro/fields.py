"""Makro DOM control capture, scrolling and semantic field grouping.

This module owns the deterministic field-discovery logic of the Makro domain
adapter layer. It contains no category-specific field lists: attribute keys are
derived from stable DOM ids (or indexed names as a fallback) at runtime.
"""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Page

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

