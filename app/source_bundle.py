from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook

from .data_loader import load_products


QUESTION_HEADERS = {
    "question",
    "questions",
    "attribute",
    "attribute name",
    "field",
    "field name",
    "property",
    "问题",
    "属性",
    "属性名",
    "字段",
    "字段名",
}
ANSWER_HEADERS = {
    "answer",
    "answers",
    "value",
    "attribute value",
    "答案",
    "回答",
    "值",
    "属性值",
}


def normalize_key(value: object) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.casefold())


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


@dataclass(slots=True, frozen=True)
class SourceEvidence:
    """One explicit piece of product evidence.

    The resolver never invents product facts. Every resolved answer must point
    back to one or more SourceEvidence objects.
    """

    key: str
    value: str | tuple[str, ...]
    source_type: str
    source_reference: str
    priority: int
    confidence: float = 1.0
    note: str = ""

    @property
    def normalized_key(self) -> str:
        return normalize_key(self.key)


@dataclass(slots=True)
class ProductSourceBundle:
    sku: str = ""
    evidence: list[SourceEvidence] = field(default_factory=list)
    image_paths: tuple[str, ...] = ()
    product_url: str | None = None
    supplemental_text: str = ""

    def add_evidence(
        self,
        *,
        key: str,
        value: str | Iterable[str],
        source_type: str,
        source_reference: str,
        priority: int,
        confidence: float = 1.0,
        note: str = "",
    ) -> None:
        if isinstance(value, str):
            stored: str | tuple[str, ...] = value.strip()
            if not stored:
                return
        else:
            stored = tuple(str(item).strip() for item in value if str(item).strip())
            if not stored:
                return
        self.evidence.append(
            SourceEvidence(
                key=key.strip(),
                value=stored,
                source_type=source_type,
                source_reference=source_reference,
                priority=priority,
                confidence=confidence,
                note=note,
            )
        )

    def candidates(self, keys: Iterable[str]) -> list[SourceEvidence]:
        wanted = {normalize_key(key) for key in keys if key}
        if not wanted:
            return []
        return [item for item in self.evidence if item.normalized_key in wanted]


def bundle_from_product_table(
    path: str | Path,
    *,
    sku: str | None = None,
    image_paths: Iterable[str] = (),
    product_url: str | None = None,
    supplemental_text: str = "",
) -> ProductSourceBundle:
    """Load one product row from the existing CSV/XLSX product-table format."""

    source = Path(path)
    records = load_products(source)
    if sku:
        matches = [record for record in records if record.sku == sku]
        if not matches:
            raise ValueError(f"数据文件中未找到 SKU={sku}")
        record = matches[0]
    elif len(records) == 1:
        record = records[0]
    else:
        raise ValueError("数据文件包含多个商品，请通过 --sku 指定一个 SKU。")

    bundle = ProductSourceBundle(
        sku=record.sku,
        image_paths=tuple(str(item) for item in image_paths),
        product_url=product_url,
        supplemental_text=supplemental_text,
    )
    bundle.add_evidence(
        key="SKU",
        value=record.sku,
        source_type="structured",
        source_reference=f"{source.name}:row={record.row_number}",
        priority=10,
    )
    for key, value in record.values.items():
        bundle.add_evidence(
            key=key,
            value=value,
            source_type="structured",
            source_reference=f"{source.name}:row={record.row_number}:column={key}",
            priority=10,
        )
    return bundle


def _find_header(headers: list[str], aliases: set[str]) -> int | None:
    normalized_aliases = {normalize_key(item) for item in aliases}
    for index, header in enumerate(headers):
        if normalize_key(header) in normalized_aliases:
            return index
    return None


def _qa_rows_from_csv(path: Path) -> tuple[list[str], list[list[Any]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise ValueError("QA CSV 文件为空。")
    return [_stringify(item) for item in rows[0]], rows[1:]


def _qa_rows_from_excel(path: Path) -> tuple[list[str], list[list[Any]]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise ValueError("QA Excel 文件为空。")
        return [_stringify(item) for item in rows[0]], [list(row) for row in rows[1:]]
    finally:
        workbook.close()


def bundle_from_qa_file(
    path: str | Path,
    *,
    sku: str = "",
    image_paths: Iterable[str] = (),
    product_url: str | None = None,
    supplemental_text: str = "",
) -> ProductSourceBundle:
    """Load a question/answer workbook like the client's current manual workflow.

    The loader is intentionally tolerant of English/Chinese header names but
    conservative about content: it only imports rows with both an explicit
    question/attribute and an explicit answer/value.
    """

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        headers, rows = _qa_rows_from_csv(source)
    elif suffix in {".xlsx", ".xlsm"}:
        headers, rows = _qa_rows_from_excel(source)
    else:
        raise ValueError("QA 文件当前仅支持 .csv / .xlsx / .xlsm。")

    question_index = _find_header(headers, QUESTION_HEADERS)
    answer_index = _find_header(headers, ANSWER_HEADERS)
    if question_index is None or answer_index is None:
        raise ValueError(
            "未识别到 QA 列。请至少包含 Question/Attribute/字段 与 Answer/Value/答案 列。"
        )

    bundle = ProductSourceBundle(
        sku=sku,
        image_paths=tuple(str(item) for item in image_paths),
        product_url=product_url,
        supplemental_text=supplemental_text,
    )
    for row_number, row in enumerate(rows, start=2):
        padded = list(row) + [None] * max(0, len(headers) - len(row))
        question = _stringify(padded[question_index])
        answer = _stringify(padded[answer_index])
        if not question or not answer:
            continue
        bundle.add_evidence(
            key=question,
            value=answer,
            source_type="customer_file",
            source_reference=f"{source.name}:row={row_number}",
            priority=20,
        )
    if not bundle.evidence:
        raise ValueError("QA 文件中没有可用的明确答案。")
    return bundle
