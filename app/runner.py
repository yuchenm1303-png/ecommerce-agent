from __future__ import annotations

from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from .extractor import extract_form_fields
from .filler import fill_field
from .logger import JsonlRunLogger
from .matcher import match_answer
from .models import FieldExecutionResult, ProductExecutionResult, ProductRecord
from .platforms.base import PlatformAdapter
from .validator import validate_field


class AutomationRunner:
    def __init__(
        self,
        adapter: PlatformAdapter,
        *,
        headless: bool = False,
        dry_run: bool = False,
        timeout_ms: int = 15_000,
        logs_dir: str | Path = "logs",
    ) -> None:
        self.adapter = adapter
        self.headless = headless
        self.dry_run = dry_run
        self.timeout_ms = timeout_ms
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.log_path = Path(logs_dir) / f"run-{timestamp}.jsonl"
        self.logger = JsonlRunLogger(self.log_path)

    def run(self, products: list[ProductRecord]) -> dict[str, int]:
        summary = {"success": 0, "needs_review": 0, "dry_run": 0, "error": 0}

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            try:
                for index, product in enumerate(products, start=1):
                    print(f"[{index}/{len(products)}] 处理 SKU={product.sku}")
                    result = self._run_one(page, product)
                    summary[result.status] = summary.get(result.status, 0) + 1
                    self.logger.write(result.as_dict())
                    print(f"    -> {result.status}: {result.detail or ''}")
            finally:
                context.close()
                browser.close()

        return summary

    def _run_one(self, page: Page, product: ProductRecord) -> ProductExecutionResult:
        result = ProductExecutionResult(sku=product.sku, status="error")

        try:
            self.adapter.open_product(page, product)
            if not self.adapter.verify_product(page, product):
                result.detail = "商品身份校验失败，已阻止填写。"
                return result

            page_fields = extract_form_fields(page)
            if not page_fields:
                result.detail = "页面未发现可安全识别的表单字段。"
                return result

            block_save = False
            matched_count = 0

            for field in page_fields:
                matched = match_answer(field.label, product.values)
                if matched is None:
                    result.fields.append(
                        FieldExecutionResult(
                            label=field.label,
                            status="unmatched",
                            detail="表格中未找到可靠匹配答案。",
                        )
                    )
                    if field.required:
                        block_save = True
                    continue

                matched_count += 1
                try:
                    fill_field(page, field, matched.answer)
                    valid, actual = validate_field(page, field, matched.answer)
                    if not valid:
                        block_save = True
                        result.fields.append(
                            FieldExecutionResult(
                                label=field.label,
                                status="validation_failed",
                                answer=matched.answer,
                                source_header=matched.source_header,
                                detail=f"填写后读取到的实际值为：{actual}",
                            )
                        )
                        continue

                    result.fields.append(
                        FieldExecutionResult(
                            label=field.label,
                            status="validated",
                            answer=matched.answer,
                            source_header=matched.source_header,
                            detail=f"匹配策略={matched.strategy}, 置信度={matched.confidence:.2f}",
                        )
                    )
                except Exception as exc:
                    block_save = True
                    result.fields.append(
                        FieldExecutionResult(
                            label=field.label,
                            status="fill_error",
                            answer=matched.answer,
                            source_header=matched.source_header,
                            detail=str(exc),
                        )
                    )

            if matched_count == 0:
                result.status = "needs_review"
                result.detail = "没有任何字段获得可靠匹配，已阻止保存。"
                return result

            if block_save:
                result.status = "needs_review"
                result.detail = "存在未匹配的必填字段或校验失败字段，已阻止保存。"
                return result

            if self.dry_run:
                result.status = "dry_run"
                result.detail = "字段已填写并校验；dry-run 模式未点击保存。"
                return result

            save_message = self.adapter.save(page)
            result.status = "success"
            result.detail = save_message
            return result

        except Exception as exc:
            result.status = "error"
            result.detail = str(exc)
            return result
