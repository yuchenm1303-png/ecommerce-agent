from __future__ import annotations

from urllib.parse import quote

from playwright.sync_api import Page

from app.models import ProductRecord
from app.platforms.base import PlatformAdapter


class MockPlatformAdapter(PlatformAdapter):
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def open_product(self, page: Page, product: ProductRecord) -> None:
        page.goto(f"{self.base_url}/?sku={quote(product.sku)}", wait_until="domcontentloaded")
        page.locator("#product-form").wait_for(state="visible")

    def verify_product(self, page: Page, product: ProductRecord) -> bool:
        actual_sku = page.locator("#sku-value").inner_text().strip()
        return actual_sku == product.sku

    def save(self, page: Page) -> str:
        page.locator("#save-button").click()
        status = page.locator('#save-status[data-state="success"]')
        status.wait_for(state="visible")
        return status.inner_text().strip()
