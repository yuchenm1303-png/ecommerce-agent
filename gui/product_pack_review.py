from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ai_decisions import field_id

from .result_loader import RunResult, latest_live_schema


@dataclass(slots=True, frozen=True)
class ConflictAlternative:
    values: tuple[str, ...]
    qualifier: str
    sources: tuple[str, ...]

    @property
    def display_value(self) -> str:
        value = " + ".join(self.values) or "—"
        return f"{value} {self.qualifier}".strip()


@dataclass(slots=True, frozen=True)
class ConflictReview:
    field_id: str
    field_name: str
    required: bool
    alternatives: tuple[ConflictAlternative, ...]


@dataclass(slots=True, frozen=True)
class FieldReview:
    field_id: str
    field_name: str
    ai_status: str
    ai_result: str
    final_status: str
    sources: tuple[str, ...]


@dataclass(slots=True)
class ProductPackReviewModel:
    original_files: tuple[str, ...] = ()
    stored_file_count: int = 0
    parsed_snapshot_count: int = 0
    evidence_image_count: int = 0
    listing_image_count: int = 0
    listing_image_rejected: int = 0
    warnings: tuple[str, ...] = ()
    fields: list[FieldReview] = field(default_factory=list)
    conflicts: list[ConflictReview] = field(default_factory=list)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _path(value: Any) -> Path | None:
    text = str(value or "").strip()
    return Path(text) if text else None


def _decision_value(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "").strip().casefold()
    values = [str(value).strip() for value in payload.get("values") or [] if str(value).strip()]
    qualifier = str(payload.get("qualifier") or "").strip()
    if status in {"ready", "review"}:
        return (" + ".join(values) + (f" {qualifier}" if qualifier else "")).strip() or status.upper()
    if status == "conflict":
        rendered: list[str] = []
        for alternative in payload.get("alternatives") or []:
            if not isinstance(alternative, dict):
                continue
            alt_values = [
                str(value).strip()
                for value in alternative.get("values") or []
                if str(value).strip()
            ]
            alt_qualifier = str(alternative.get("qualifier") or "").strip()
            text = " + ".join(alt_values)
            if alt_qualifier:
                text = f"{text} {alt_qualifier}".strip()
            if text:
                rendered.append(text)
        return " ↔ ".join(rendered) or "CONFLICT"
    return status.upper() or "—"


def _citation_refs(payload: dict[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for citation in payload.get("citations") or []:
        if not isinstance(citation, dict):
            continue
        ref = str(citation.get("source_reference") or "").strip()
        if ref:
            refs.append(ref)
    return tuple(dict.fromkeys(refs))


def _source_label_map(
    source_manifest: dict[str, Any],
    pack_manifest: dict[str, Any],
) -> dict[str, str]:
    stored_by_name: dict[str, str] = {}
    for item in pack_manifest.get("stored_files") or []:
        if not isinstance(item, dict):
            continue
        stored = str(item.get("stored_path") or "").strip()
        original = str(item.get("original_path") or "").strip()
        if stored and original:
            stored_by_name[Path(stored).name] = original

    output: dict[str, str] = {}
    for item in source_manifest.get("sources") or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "").strip()
        if not source_id:
            continue
        origin = unquote(str(item.get("origin") or "").strip())
        label = origin or source_id
        for stored_name, original in stored_by_name.items():
            if stored_name and stored_name in origin:
                if "::" in original:
                    archive, child = original.split("::", 1)
                    label = f"{Path(archive).name} › {child}"
                else:
                    label = Path(original).name
                break
        fragment = ""
        if "#" in origin:
            fragment = origin.split("#", 1)[1]
        if fragment:
            useful = [
                token
                for token in fragment.replace("#", "&").split("&")
                if token.startswith(("page=", "sheet=", "table=", "row="))
            ]
            if useful:
                label += " · " + " · ".join(useful)
        output[source_id] = label
    return output


def _resolved_sources(payload: dict[str, Any], labels: dict[str, str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(labels.get(ref, ref) for ref in _citation_refs(payload)))


def load_product_pack_review(result: RunResult) -> ProductPackReviewModel:
    workflow_path = result.run_dir / "run-manifest.json"
    if not workflow_path.is_file():
        raise RuntimeError("当前 run 缺少 run-manifest.json。")
    workflow = _read_json(workflow_path)
    if str(workflow.get("input_mode") or "") != "customer_product_pack":
        raise RuntimeError("当前结果不是客户 Product Pack 任务。")

    outputs = result.resolver.get("outputs") or {}
    if not isinstance(outputs, dict):
        outputs = {}
    pack_path = _path(outputs.get("product_pack_manifest"))
    if pack_path is None or not pack_path.is_file():
        product_input = workflow.get("product_input") or {}
        pack_path = _path(product_input.get("product_pack_manifest") if isinstance(product_input, dict) else "")
    if pack_path is None or not pack_path.is_file():
        raise RuntimeError("当前 Product Pack run 缺少 product-pack.json。")
    pack_manifest = _read_json(pack_path)

    source_manifest_path = _path(outputs.get("source_manifest"))
    source_manifest = (
        _read_json(source_manifest_path)
        if source_manifest_path is not None and source_manifest_path.is_file()
        else {}
    )
    labels = _source_label_map(source_manifest, pack_manifest)

    decision_path = _path(outputs.get("final_decisions"))
    decisions_payload = (
        _read_json(decision_path)
        if decision_path is not None and decision_path.is_file()
        else {}
    )
    decisions = [
        item for item in decisions_payload.get("decisions") or [] if isinstance(item, dict)
    ]

    live_schema_path = latest_live_schema(result.run_dir)
    fields: list[dict[str, Any]] = []
    if live_schema_path is not None and live_schema_path.is_file():
        raw = json.loads(live_schema_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            fields = [item for item in raw if isinstance(item, dict)]
        elif isinstance(raw, dict):
            fields = [
                item
                for item in (raw.get("fields") or raw.get("items") or [])
                if isinstance(item, dict)
            ]
    field_by_id = {field_id(item): item for item in fields}
    result_by_id = {row.field_id: row for row in result.fields}

    reviewed_fields: list[FieldReview] = []
    conflicts: list[ConflictReview] = []
    for decision in decisions:
        identifier = str(decision.get("field_id") or "").strip()
        live_field = field_by_id.get(identifier, {})
        row = result_by_id.get(identifier)
        name = (
            row.field_name
            if row is not None
            else str(live_field.get("label") or live_field.get("attribute_key") or identifier)
        )
        payloads = [decision]
        payloads.extend(
            item for item in decision.get("alternatives") or [] if isinstance(item, dict)
        )
        sources = tuple(
            dict.fromkeys(
                label
                for payload in payloads
                for label in _resolved_sources(payload, labels)
                if label
            )
        )
        reviewed_fields.append(
            FieldReview(
                field_id=identifier,
                field_name=name,
                ai_status=str(decision.get("status") or "").upper() or "—",
                ai_result=_decision_value(decision),
                final_status=row.final_status if row is not None else "—",
                sources=sources,
            )
        )
        if str(decision.get("status") or "").casefold() != "conflict":
            continue
        alternatives: list[ConflictAlternative] = []
        for alternative in decision.get("alternatives") or []:
            if not isinstance(alternative, dict):
                continue
            values = tuple(
                str(value).strip()
                for value in alternative.get("values") or []
                if str(value).strip()
            )
            if not values:
                continue
            alternatives.append(
                ConflictAlternative(
                    values=values,
                    qualifier=str(alternative.get("qualifier") or "").strip(),
                    sources=_resolved_sources(alternative, labels),
                )
            )
        conflicts.append(
            ConflictReview(
                field_id=identifier,
                field_name=name,
                required=bool(live_field.get("required")),
                alternatives=tuple(alternatives),
            )
        )

    originals: list[str] = []
    for item in pack_manifest.get("stored_files") or []:
        if not isinstance(item, dict):
            continue
        original = str(item.get("original_path") or "").strip()
        if not original or "::" in original:
            continue
        if original not in originals:
            originals.append(original)

    return ProductPackReviewModel(
        original_files=tuple(originals),
        stored_file_count=len(pack_manifest.get("stored_files") or []),
        parsed_snapshot_count=len(pack_manifest.get("customer_snapshots") or []),
        evidence_image_count=len(pack_manifest.get("evidence_images") or []),
        listing_image_count=len(pack_manifest.get("listing_images") or []),
        listing_image_rejected=int(pack_manifest.get("listing_image_rejected") or 0),
        warnings=tuple(str(value) for value in pack_manifest.get("warnings") or [] if str(value).strip()),
        fields=reviewed_fields,
        conflicts=conflicts,
    )


class ProductPackReviewDialog(QDialog):
    """Inspect exact Product Pack provenance and confirm blocking conflicts."""

    def __init__(self, result: RunResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.result = result
        self.model = load_product_pack_review(result)
        self.confirmed_count = 0
        self.setWindowTitle("Product Pack · 解析结果与证据来源")
        self.resize(1180, 760)
        self.setMinimumSize(920, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)

        title = QLabel("客户资料解析结果 · 字段证据可追溯")
        title.setObjectName("cardTitle")
        summary = QLabel(
            f"原始文件 {len(self.model.original_files)} · 规范化来源 {self.model.parsed_snapshot_count} · "
            f"证据图片 {self.model.evidence_image_count} · Listing 图片 {self.model.listing_image_count} · "
            f"冲突字段 {len(self.model.conflicts)}"
        )
        summary.setObjectName("cardHint")
        summary.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(summary)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_fields_tab(), "字段与来源")
        self.tabs.addTab(self._build_conflicts_tab(), f"冲突确认 · {len(self.model.conflicts)}")
        self.tabs.addTab(self._build_files_tab(), "资料清单")
        layout.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        self.status_label = QLabel("所有确认只写入当前 run 的显式用户 override；不会触发 AI，也不会操作浏览器。")
        self.status_label.setObjectName("cardHint")
        self.status_label.setWordWrap(True)
        bottom.addWidget(self.status_label, 1)
        close_button = QPushButton("完成")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(self.accept)
        bottom.addWidget(close_button)
        layout.addLayout(bottom)

    @staticmethod
    def _item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(str(text or ""))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _build_fields_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        table = QTableWidget(len(self.model.fields), 5)
        table.setHorizontalHeaderLabels(["字段", "AI 状态", "解析结果", "最终 Gate", "证据来源"])
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for row, field in enumerate(self.model.fields):
            table.setItem(row, 0, self._item(field.field_name))
            table.setItem(row, 1, self._item(field.ai_status))
            table.setItem(row, 2, self._item(field.ai_result))
            table.setItem(row, 3, self._item(field.final_status))
            table.setItem(row, 4, self._item(" | ".join(field.sources) or "—"))
        layout.addWidget(table)
        return host

    def _build_conflicts_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel(
            "只有 required 且仍 BLOCKED 的冲突需要人工确认才影响 Full Step 3。"
            "确认会采用 Resolver 已给出的某一个完整 alternative（含 qualifier），随后仍由 executor 做 live option / unit 校验。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("cardHint")
        layout.addWidget(hint)

        table = QTableWidget(len(self.model.conflicts), 5)
        self.conflict_table = table
        table.setHorizontalHeaderLabels(["字段", "Gate", "选择一个 alternative", "alternative 来源", "确认"])
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        support = getattr(self.parent(), "_required_input_support", None)
        for row, conflict in enumerate(self.model.conflicts):
            table.setItem(row, 0, self._item(conflict.field_name))
            gate = "REQUIRED · needs confirmation" if conflict.required else "optional · skipped if unresolved"
            table.setItem(row, 1, self._item(gate))

            combo = QComboBox()
            for alternative in conflict.alternatives:
                combo.addItem(alternative.display_value, alternative)
            combo.setEnabled(bool(conflict.alternatives))
            table.setCellWidget(row, 2, combo)

            source_item = self._item(
                " | ".join(
                    f"{index + 1}) {' | '.join(alternative.sources) or '—'}"
                    for index, alternative in enumerate(conflict.alternatives)
                )
            )
            table.setItem(row, 3, source_item)

            button = QPushButton("确认所选值")
            button.setObjectName("quietButton")
            can_confirm = bool(
                conflict.required
                and conflict.alternatives
                and support is not None
                and conflict.field_id in getattr(support, "fields", {})
            )
            button.setEnabled(can_confirm)
            if not conflict.required:
                button.setText("非必填 · 无需确认")
            elif not conflict.alternatives:
                button.setText("无可确认 alternative")
            elif not can_confirm:
                button.setText("当前 Gate 不需补值")
            button.clicked.connect(
                lambda _checked=False, r=row, c=conflict, box=combo: self._confirm_conflict(r, c, box)
            )
            table.setCellWidget(row, 4, button)

        layout.addWidget(table, 1)
        return host

    def _build_files_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        table = QTableWidget(len(self.model.original_files), 2)
        table.setHorizontalHeaderLabels(["原始资料", "解析状态"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for row, value in enumerate(self.model.original_files):
            table.setItem(row, 0, self._item(value))
            table.setItem(row, 1, self._item("已持久化"))
        layout.addWidget(table, 1)
        footer = QLabel(
            f"stored entries={self.model.stored_file_count} · parsed snapshots={self.model.parsed_snapshot_count} · "
            f"listing image rejected={self.model.listing_image_rejected}"
        )
        footer.setObjectName("cardHint")
        layout.addWidget(footer)
        if self.model.warnings:
            warning = QLabel("解析警告：" + " | ".join(self.model.warnings))
            warning.setWordWrap(True)
            warning.setObjectName("cardHint")
            layout.addWidget(warning)
        return host

    def _confirm_conflict(self, row: int, conflict: ConflictReview, combo: QComboBox) -> None:
        alternative = combo.currentData()
        if not isinstance(alternative, ConflictAlternative):
            QMessageBox.warning(self, "无法确认", "当前冲突没有可用 alternative。")
            return
        parent = self.parent()
        support = getattr(parent, "_required_input_support", None)
        if support is None:
            QMessageBox.warning(self, "无法确认", "必填字段确认支持尚未初始化。")
            return
        applied = support.set_explicit_override(
            conflict.field_id,
            list(alternative.values),
            qualifier=alternative.qualifier,
        )
        if not applied:
            QMessageBox.warning(self, "无法确认", "该字段已不属于当前 unresolved required Gate，请重新运行解析结果。")
            return
        self.confirmed_count += 1
        button = self.conflict_table.cellWidget(row, 4)
        if isinstance(button, QPushButton):
            button.setText("已确认")
            button.setEnabled(False)
        self.status_label.setText(
            f"已确认 {self.confirmed_count} 个 required 冲突。值仅保存在当前任务内，真实执行前仍会重新校验。"
        )


__all__ = [
    "ConflictAlternative",
    "ConflictReview",
    "FieldReview",
    "ProductPackReviewDialog",
    "ProductPackReviewModel",
    "load_product_pack_review",
]
