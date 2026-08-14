from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QLineEdit, QMessageBox

from app.required_overrides import (
    load_required_blocked_fields,
    required_fallback_override,
    required_override_binding,
)
from .real_execution import FULL_STEP3
from .result_loader import RunResult, latest_fill_plan, latest_live_schema


class RequiredInputSupport(QObject):
    """Automatically cover unresolved required Makro fields before Full Step 3.

    The normal Resolver remains authoritative. Required fields still BLOCKED
    after that pass stay visible in the field table, but they never force the
    user to run another AI pass or manually type values before execution.

    At Full Step 3 start, every still-empty required field receives a purely
    deterministic fallback derived from the current live schema:
    - select/radio fields: first usable Makro option;
    - numeric/unit fields: ``1`` (plus first usable qualifier when applicable);
    - other free-text fields: ``N/A``.

    A value typed by the user remains an optional override and wins over the
    deterministic fallback. Placeholder text itself is never copied as input.

    Qt editors are presentation-only. QTableWidget owns and may destroy cell
    widgets whenever the table is rebuilt, so authoritative manual values and
    required-field bindings live in plain Python state and never depend on a
    QLineEdit remaining alive.
    """

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        # Ephemeral view registry only. Never use these widgets as business state.
        self.inputs: dict[str, QLineEdit] = {}
        self.values: dict[str, str] = {}
        self.labels: dict[str, str] = {}
        self.fields: dict[str, dict[str, Any]] = {}
        self._original_start = window._start_real_execution

        window.runner.result_updated.connect(self._on_result)
        window.runner.completed.connect(lambda _result: self._sync_button())
        window.execution_runner.running_changed.connect(lambda _running: self._sync_button())
        window.real_scope_combo.currentIndexChanged.connect(lambda _index: self._sync_button())

        try:
            window.real_start_button.clicked.disconnect()
        except Exception:
            pass
        window.real_start_button.clicked.connect(self.request_start)
        window._request_real_execution = self.request_start

    def _required_blocked(self, result: RunResult) -> list[dict[str, Any]]:
        plan_path = latest_fill_plan(result.run_dir)
        schema_path = latest_live_schema(result.run_dir)
        if plan_path is None or schema_path is None or not plan_path.is_file() or not schema_path.is_file():
            return []
        return load_required_blocked_fields(plan_path, schema_path)

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
        # Preserve user-entered values from the Python model, never from old
        # QLineEdit wrappers: QTableWidget may already have deleted those C++
        # objects while rebuilding the result table.
        previous_values = dict(self.values)
        self.inputs = {}
        self.values = {}
        self.labels = {}
        self.fields = {}

        required = self._required_blocked(result)
        for missing in required:
            identifier = missing["field_id"]
            field = missing["field"]

            # Required fallback is business state and must exist even if the
            # corresponding table row is temporarily not rendered/filtered.
            self.values[identifier] = previous_values.get(identifier, "")
            self.labels[identifier] = missing["label"]
            self.fields[identifier] = field

            row = self._table_row_for_field_id(identifier)
            if row is None:
                continue

            fallback = required_fallback_override(field)
            fallback_values = [
                str(value).strip()
                for value in fallback.get("values") or []
                if str(value).strip()
            ]
            fallback_text = " + ".join(fallback_values) or "N/A"
            qualifier = str(fallback.get("qualifier") or "").strip()
            if qualifier:
                fallback_text = f"{fallback_text} {qualifier}".strip()

            editor = QLineEdit()
            editor.setPlaceholderText(f"必填 · 留空将自动填 {fallback_text}")
            if self.values[identifier]:
                editor.setText(self.values[identifier])
            options = missing.get("options") or []
            tooltip = missing.get("reason") or "正常 Resolver 未能确定该必填字段。"
            tooltip += (
                "\n\n无需再次运行 AI，也无需先手动补齐。"
                f"Full Step 3 开始前若仍留空，将机械写入兜底值：{fallback_text}。"
            )
            if options:
                tooltip += "\n\nMakro 可选值：\n" + " | ".join(options)
            editor.setToolTip(tooltip)
            editor.textChanged.connect(
                lambda text, fid=identifier: self._input_changed(fid, text)
            )
            self.window.field_table.setCellWidget(row, 2, editor)
            self.inputs[identifier] = editor

        if required:
            self.window.fields_hint.setText(
                f"READY={result.ready} · {len(required)} 个必填缺口会在真实填写前自动兜底"
            )
            self.window.real_policy_hint.setText(
                f"还有 {len(required)} 个 Makro 必填项未由正常 Resolver 确定。可以直接开始 Full Step 3；"
                "不会再调用 AI。留空项会自动使用固定兜底值：自由文本 N/A、数字 1、下拉/单选取第一个有效选项。"
            )
        self._sync_button()

    def _input_changed(self, field_id: str, text: str) -> None:
        # A stale editor can emit during a table rebuild. Ignore it once its
        # field no longer belongs to the current result model.
        if field_id not in self.fields:
            return
        self.values[field_id] = str(text or "")
        self._sync_button()

    def _manual_count(self) -> int:
        return sum(
            bool(self.values.get(identifier, "").strip())
            for identifier in self.fields
        )

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
        if scope == FULL_STEP3 and self.fields:
            self.window.real_start_button.setEnabled(result.ready > 0 or bool(self.fields))
            manual = self._manual_count()
            automatic = len(self.fields) - manual
            self.window.real_start_button.setToolTip(
                f"可直接开始；{manual} 个使用手动值，{automatic} 个留空必填项将自动使用固定兜底值。"
            )
            return

        self.window.real_start_button.setEnabled(result.ready > 0)
        if result.ready <= 0:
            self.window.real_start_button.setToolTip("当前 Fill Plan 没有 READY 字段。")

    def _merged_overrides(self) -> list[dict[str, Any]]:
        overrides: list[dict[str, Any]] = []
        for identifier, field in self.fields.items():
            value = self.values.get(identifier, "").strip()
            if value:
                overrides.append(
                    {
                        **required_override_binding(field),
                        "values": [value],
                        "source_type": "user",
                    }
                )
            else:
                overrides.append(required_fallback_override(field))
        return overrides

    def _write_overrides(self) -> Path | None:
        result = getattr(self.window, "current_result", None)
        if result is None:
            return None
        schema_path = latest_live_schema(result.run_dir)
        if schema_path is None:
            raise RuntimeError("找不到当前 run 的 live schema，无法保存必填补充值。")
        path = schema_path.with_name("required-overrides.json")
        overrides = self._merged_overrides()
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
        """Run the canonical preflight and automatically cover required gaps."""

        result = getattr(self.window, "current_result", None)
        if self.window.runner.is_running or self.window.execution_runner.is_running:
            QMessageBox.warning(self.window, "无法开始真实填写", "当前已有准备流程或真实执行正在运行。")
            return
        if result is None or not result.plan_summary:
            QMessageBox.warning(self.window, "无法开始真实填写", "请先完成 Step 3 Resolver + Fill Plan。")
            return
        if result.ready <= 0 and not self.fields:
            QMessageBox.warning(self.window, "没有可填写字段", "当前 Fill Plan 没有 READY 或待兜底的必填字段，真实填写保持锁定。")
            return

        scope = self.window.real_scope_combo.currentData()
        try:
            if scope == FULL_STEP3:
                path = self._write_overrides()
                if self.fields:
                    manual = self._manual_count()
                    automatic = len(self.fields) - manual
                    self.window.fields_hint.setText(
                        f"必填预检完成 · 手动 {manual} · 固定兜底 {automatic}"
                    )
                    self.window.real_policy_hint.setText(
                        "Full Step 3 将直接继续。未解决必填项已用非 AI 的固定兜底策略补齐；"
                        "执行器仍会在浏览器写入前校验当前 Makro option / unit 合法性。"
                    )
                    append = getattr(self.window, "_append_log", None)
                    if callable(append):
                        append(
                            f"[required-fallback] overrides={path or 'none'} "
                            f"manual={manual} automatic={automatic} ai_calls=0"
                        )
            else:
                schema_path = latest_live_schema(result.run_dir)
                if schema_path is not None:
                    stale = schema_path.with_name("required-overrides.json")
                    if stale.exists():
                        stale.unlink()
        except Exception as exc:
            QMessageBox.critical(self.window, "无法生成必填兜底值", str(exc))
            return

        self._original_start()

    def _on_start_clicked(self, _checked: bool = False) -> None:
        self.request_start(_checked)


def install_required_input_support(window: Any) -> RequiredInputSupport:
    support = RequiredInputSupport(window)
    window._required_input_support = support
    return support
