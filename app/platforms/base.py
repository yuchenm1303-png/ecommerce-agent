from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.sync_api import Page

from app.models import ProductRecord


class PlatformAdapter(ABC):
    @abstractmethod
    def open_product(self, page: Page, product: ProductRecord) -> None:
        """Navigate to the edit page for one product."""

    @abstractmethod
    def verify_product(self, page: Page, product: ProductRecord) -> bool:
        """Confirm the page belongs to the intended product before editing."""

    @abstractmethod
    def save(self, page: Page) -> str:
        """Submit the form and return a human-readable success message."""
