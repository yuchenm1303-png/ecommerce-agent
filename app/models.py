from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProductRecord:
    sku: str
    values: dict[str, str]
    row_number: int


@dataclass(slots=True)
class PageField:
    label: str
    selector: str
    control_type: str
    required: bool = False


@dataclass(slots=True)
class MatchResult:
    source_header: str
    answer: str
    strategy: str
    confidence: float


@dataclass(slots=True)
class FieldExecutionResult:
    label: str
    status: str
    answer: str | None = None
    source_header: str | None = None
    detail: str | None = None


@dataclass(slots=True)
class ProductExecutionResult:
    sku: str
    status: str
    fields: list[FieldExecutionResult] = field(default_factory=list)
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "status": self.status,
            "detail": self.detail,
            "fields": [
                {
                    "label": item.label,
                    "status": item.status,
                    "answer": item.answer,
                    "source_header": item.source_header,
                    "detail": item.detail,
                }
                for item in self.fields
            ],
        }
