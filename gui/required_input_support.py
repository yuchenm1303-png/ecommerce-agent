from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QLineEdit, QMessageBox

from app.ai_decisions import field_id
from .real_execution import FULL_STEP3
from .result_loader import RunResult, latest_fill_plan, latest_live_schema


class RequiredInputSupport(QObject):
    """Expose unresolved required Makro fields as user inputs in the existing table.

    The normal Resolver remains authoritative. Only required fields that are
    still BLOCKED after that pass get an input. Placeholder text is GUI-only and
    is never written to Makro; only non-empty text explicitly entered by the
    user is persisted to required-overrides.json for the production executor.

    ``request_start`` is the canonical GUI execution request. Both the preserved
    main button and any detail-panel action call this exact preflight directly;
    no action simulates a click on another hidden QPushButton.
    """

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.inputs: dict[str, QLineEdit] = {}
        self.labels: dict[str, str] = {}
        self._original_start = window._start_real_execution

        window.runner.result_updated.connect(self._on_result)
        window.runner.completed.connect(lambda _result: self._sync_button())
        window.execution_runner.running_changed.connect(lambda _running: self._sync_button())
        window.real_scope_combo.currentIndexChanged.connect(lambda _index: self._sync_button())

        # Replace the construction-time action with one canonical preflight.
        try:
            window.real_start_button.clicked.disconnect()
        except Exception:
            pass
        window.real_start_button.clicked.connect(self.request_start)
        window._request_real_execution = self.request_start

    @staticmethod
    def _identity(payload: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(payload.get("attribute_key") or ""),
            str(payload.get("label") or payload.get("attribute_key") or ""),
            str(payload.get("section_heading") or ""),
        )

    def _required_blocked(self, result: RunResult) -> list[dict[str, Any]]:
        plan_path = latest_fill_plan(result.run_dir)
        schema_path = latest_live_schema(result.run_dir)
        if plan_path is None or schema_path is None or not plan_path.is_file() or not schema_path.is_file():
            return []

        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
        raw_schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if isinstance(raw_schema, dict):
            fields = raw_schema.get("fields") or raw_schema.get("items") or []
        else:
            fields = raw_schema
        fields = [item for item in fields if isinstance(item, dict)]
        by_identity = {self._identity(field): field for field in fields}

        output: list[dict[str, Any]] = []
        for item in plan_payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("required")) or str(item.get("action") or "").casefold() != "blocked":
                continue
            field = by_identity.get(self._identity(item))
            if field is None:
                continue
            resolution = item.get("resolution") or {}
            output.append(
                {
                    "field_id": field_id(field),
                    "label": str(item.get("label") or item.get("attribute_key") or "必填字段"),
                    "reason": str(item.get("reason") or resolution.get("detail") or "").strip(),
                    "options": [
                        str(value).strip()
                        for value in resolution.get("question_options") or []
                        if str(value).strip() and str(value).strip().casefold() not in {"select one", "select"}
                    ],
                }
            )
        return output

    def _table_row_for_field_id(self, identifier: str) -> int | None:
        table = self.window.field_table
        if table.columnCount() < 7:
            return None
        for row in range(table.rowCount()):
            cell = table.item(row, 6)
            if cell is not None and cell.text().strip() == identifier:
                return row
        return None

    def _on_result(self, result: RunResult) -> None:
        previous = {identifier: editor.text() for identifier, editor in self.inputs.items()}
        self.inputs = {}
        self.labels = {}

        required = self._required_blocked(result)
        for missing in required:
            identifier = missing["field_id"]
            row = self._table_row_for_field_id(identifier)
            if row is None:
                continue
            editor = QLineEdit()
            editor.setPlaceholderText("必填 · AI 未找到，请填写")
            if previous.get(identifier):
                editor.setText(previous[identifier])
            options = missing.get("options") or []
            tooltip = missing.get("reason") or "AI/Resolver 未能确定该必填字段。"
            if options:
                tooltip += "\n\nMakro 可选值：\n" + " | ".join(options)
            editor.setToolTip(tooltip)
            editor.textChanged.connect(lambda _text, fid=identifier: self._input_changed(fid))
            self.window.field_table.setCellWidget(row, 2, editor)
            self.inputs[identifier] = editor
            self.labels[identifier] = missing["label"]

        if required:
            self.window.fields_hint.setText(
                f"READY={result.ready} · 还有 {len(required)} 个必填项需要你补充"
            )
            self.window.real_policy_hint.setText(
                f"还有 {len(required)} 个 Makro 必填项 AI 没有可靠答案。请直接在字段表的输入框补上；"
                "补齐后再执行 Full Step 3。占位提示不会写入 Makro。"
            )
        self._sync_button()

    def _input_changed(self, _field_id: str) -> None:
        self._sync_button()

    def _all_required_filled(self) -> bool:
        return all(editor.text().strip() for editor in self.inputs.values())

    def _sync_button(self) -> None:
        result = getattr(self.window, "current_result", None)
        if result is None or not result.plan_summary:
            self.window.real_start_button.setEnabled(False)
            self.window.real_start_button.setToolTip("请先完成 Step 3 Resolver + Fill Plan。")
            return
        if self.window.runner.is_running or self.window.execution_runner.is_running:
            self.window.real_start_button.setEnabled(False)
            self.window.real_start_button.setToolTip("当前已有准备流程或真实执行正在运行。")
            return
        scope = self.window.real_scope_combo.currentData()
        if scope == FULL_STEP3 and self.inputs:
            ready = self._all_required_filled()
            self.window.real_start_button.setEnabled(ready and result.ready > 0)
            if not ready:
                left = sum(not editor.text().strip() for editor in self.inputs.values())
                self.window.real_start_button.setToolTip(f"还差 {left} 个必填字段，请先在字段表补齐。")
            else:
                self.window.real_start_button.setToolTip("必填项已补齐；执行全部 READY + 用户补充字段。")
        else:
            self.window.real_start_button.setEnabled(result.ready > 0)
            if result.ready <= 0:
                self.window.real_start_button.setToolTip("当前 Fill Plan 没有 READY 字段。")

    def _write_overrides(self) -> Path | None:
        result = getattr(self.window, "current_result", None)
        if result is None:
            return None
        schema_path = latest_live_schema(result.run_dir)
        if schema_path is None:
            raise RuntimeError("找不到当前 run 的 live schema，无法保存必填补充值。")
        path = schema_path.with_name("required-overrides.json")
        overrides = [
            {
                "field_id": identifier,
                "values": [editor.text().strip()],
            }
            for identifier, editor in self.inputs.items()
            if editor.text().strip()
        ]
        if not overrides:
            if path.exists():
                path.unlink()
            return None
        path.write_text(
            json.dumps({"overrides": overrides}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def request_start(self, _checked: bool = False) -> None:
        """Run the one canonical GUI preflight, regardless of which UI invoked it."""

        result = getattr(self.window, "current_result", None)
        if self.window.runner.is_running or self.window.execution_runner.is_running:
            QMessageBox.warning(self.window, "无法开始真实填写", "当前已有准备流程或真实执行正在运行。")
            return
        if result is None or not result.plan_summary:
            QMessageBox.warning(self.window, "无法开始真实填写", "请先完成 Step 3 Resolver + Fill Plan。")
            return
        if result.ready <= 0:
            QMessageBox.warning(self.window, "没有可填写字段", "当前 Fill Plan 没有 READY 字段，真实填写保持锁定。")
            return

        scope = self.window.real_scope_combo.currentData()
        if scope == FULL_STEP3 and self.inputs and not self._all_required_filled():
            missing = [
                self.labels.get(identifier, identifier)
                for identifier, editor in self.inputs.items()
                if not editor.text().strip()
            ]
            QMessageBox.warning(
                self.window,
                "还有必填项没有填写",
                "请先补齐这些 Makro 必填字段：\n" + "\n".join(f"• {label}" for label in missing),
            )
            return
        try:
            self._write_overrides()
        except Exception as exc:
            QMessageBox.critical(self.window, "无法保存必填补充值", str(exc))
            return
        self._original_start()

    def _on_start_clicked(self, _checked: bool = False) -> None:
        # Compatibility for older callers/tests; all new UI uses request_start.
        self.request_start(_checked)


def install_required_input_support(window: Any) -> RequiredInputSupport:
    support = RequiredInputSupport(window)
    window._required_input_support = support
    return support
