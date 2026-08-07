from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from app.platforms.makro import is_makro_listing_page, parse_makro_listing_url


CONTROL_SELECTOR = 'input, textarea, select, [role="combobox"]'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在本机已授权登录后，采集 Makro Add Listing 页面字段元数据。"
    )
    parser.add_argument("--url", required=True, help="Makro Add a Single Listing 完整 URL")
    parser.add_argument(
        "--profile-dir",
        default="browser_profiles/makro",
        help="本地持久化浏览器目录；不会提交 GitHub",
    )
    parser.add_argument(
        "--output-dir",
        default="logs/makro-probe",
        help="字段快照和截图输出目录",
    )
    parser.add_argument(
        "--include-values",
        action="store_true",
        help="调试时包含当前输入值；默认关闭以减少敏感数据落盘",
    )
    return parser


def capture_controls(page: Page, include_values: bool = False) -> list[dict]:
    """Capture metadata only; deliberately excludes password/hidden controls."""

    locator = page.locator(CONTROL_SELECTOR)
    return locator.evaluate_all(
        """
        (elements, includeValues) => {
          const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
          const cssEscape = (value) => {
            if (window.CSS && CSS.escape) return CSS.escape(value);
            return value.replace(/([ #;?%&,.+*~\\':\"!^$[\\]()=>|/@])/g, '\\$1');
          };
          const isVisible = (el) => {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          };
          const nearestContext = (el) => {
            let node = el.parentElement;
            let best = '';
            for (let depth = 0; node && depth < 5; depth += 1, node = node.parentElement) {
              const text = clean(node.innerText);
              if (!text) continue;
              if (text.length <= 220) {
                best = text;
                break;
              }
            }
            return best;
          };
          const labelText = (el) => {
            const nativeLabels = Array.from(el.labels || []).map((label) => clean(label.innerText)).filter(Boolean);
            if (nativeLabels.length) return nativeLabels.join(' | ');
            const aria = clean(el.getAttribute('aria-label'));
            if (aria) return aria;
            const labelledBy = clean(el.getAttribute('aria-labelledby'));
            if (labelledBy) {
              const text = labelledBy
                .split(/\s+/)
                .map((id) => document.getElementById(id))
                .filter(Boolean)
                .map((node) => clean(node.innerText))
                .filter(Boolean)
                .join(' | ');
              if (text) return text;
            }
            const placeholder = clean(el.getAttribute('placeholder'));
            if (placeholder) return placeholder;
            return '';
          };
          const candidateSelectors = (el) => {
            const selectors = [];
            const id = el.getAttribute('id');
            const name = el.getAttribute('name');
            const testId = el.getAttribute('data-testid');
            const aria = el.getAttribute('aria-label');
            const placeholder = el.getAttribute('placeholder');
            if (id) selectors.push(`#${cssEscape(id)}`);
            if (testId) selectors.push(`[data-testid="${testId.replace(/\"/g, '\\\"')}"]`);
            if (name) selectors.push(`[name="${name.replace(/\"/g, '\\\"')}"]`);
            if (aria) selectors.push(`[aria-label="${aria.replace(/\"/g, '\\\"')}"]`);
            if (placeholder) selectors.push(`[placeholder="${placeholder.replace(/\"/g, '\\\"')}"]`);
            return selectors;
          };

          return elements.map((el, ordinal) => {
            const tag = el.tagName.toLowerCase();
            const type = clean(el.getAttribute('type') || (tag === 'select' ? 'select' : tag));
            if (type.toLowerCase() === 'password' || type.toLowerCase() === 'hidden') return null;
            if (!isVisible(el)) return null;

            const item = {
              ordinal,
              tag,
              type,
              role: clean(el.getAttribute('role')),
              id: clean(el.getAttribute('id')),
              name: clean(el.getAttribute('name')),
              placeholder: clean(el.getAttribute('placeholder')),
              aria_label: clean(el.getAttribute('aria-label')),
              aria_required: clean(el.getAttribute('aria-required')),
              required: el.required === true || el.getAttribute('aria-required') === 'true',
              label: labelText(el),
              context_text: nearestContext(el),
              selector_candidates: candidateSelectors(el),
              options: tag === 'select'
                ? Array.from(el.options || []).map((option) => clean(option.textContent)).filter(Boolean)
                : [],
            };
            if (includeValues) item.value = clean(el.value);
            return item;
          }).filter(Boolean);
        }
        """,
        include_values,
    )


def wait_for_authenticated_listing(page: Page, target_url: str) -> None:
    page.goto(target_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    if is_makro_listing_page(page):
        return

    print("\n浏览器已打开。请在这个 Playwright 浏览器窗口中手动完成 Makro 登录。")
    print("登录后进入 Add a Single Listing 页面，然后回到终端按 Enter。")
    input()

    if not is_makro_listing_page(page):
        # The user may have logged in but stayed on another page; navigate back to
        # the target route once more before failing.
        page.goto(target_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

    if not is_makro_listing_page(page):
        raise RuntimeError(
            "仍未检测到已登录的 Add a Single Listing 页面。"
            "请确认登录成功，并且页面标题包含 Add a Single Listing。"
        )


def main() -> int:
    args = build_parser().parse_args()
    target = parse_makro_listing_url(args.url)
    profile_dir = Path(args.profile_dir)
    output_dir = Path(args.output_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"makro-fields-{stamp}.json"
    screenshot_path = output_dir / f"makro-page-{stamp}.png"

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport={"width": 1600, "height": 1000},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(15_000)

        try:
            wait_for_authenticated_listing(page, target.url)
            page.wait_for_timeout(1500)
            controls = capture_controls(page, include_values=args.include_values)
            payload = {
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "platform": "makro",
                "host": "seller.makro.co.za",
                "brand": target.brand,
                "vertical": target.vertical,
                "request_id_present": bool(target.request_id),
                "vid": target.vid,
                "page_url": page.url,
                "include_values": bool(args.include_values),
                "control_count": len(controls),
                "controls": controls,
            }
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            page.screenshot(path=str(screenshot_path), full_page=True)
        finally:
            context.close()

    print(f"已采集 {payload['control_count']} 个可见控件。")
    print(f"字段元数据：{json_path}")
    print(f"页面截图：{screenshot_path}")
    print("默认没有记录密码，也没有点击 Save / Send to QC。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
