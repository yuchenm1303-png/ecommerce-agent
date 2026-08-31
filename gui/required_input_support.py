from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QLineEdit, QMessageBox

from app.required_overrides import (
    load_required_blocked_fields,
    required_override_binding,
)
from .real_execution import FULL_STEP3
from .result_loader import RunResult, latest_fill_plan, latest_live_schema


class RequiredInputSupport(QObject):
    """Collect explicit user values for required fields the AI did not resolve.

    The Resolver is the only product-semantic decision maker.  The GUI therefore
    never invents ``N/A``, ``1``, a first option, a unit conversion, or any other
    deterministic fallback after the AI pass.  AI READY fields flow straight to
    execution.  AI REVIEW/CONFLICT/MISSING required fields remain unresolved until
    the user explicitly supplies or confirms a value.

    Qt editors are presentation-only.  Authoritative manual values and bindings
    live in plain Python state so table rebuilds cannot alter business state.
    """

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.inputs: dict[str, QLineEdit] = {}
        self.values: dict[str, str] = {}
        self.labels: dict[str, str] = {}
        self.fields: dict[str, dict[str, Any]] = {}
        self.explicit_overrides: dict[str, dict[str, Any]] = {}
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
        previous_values = dict(self.values)
        self.inputs = {}
        self.values = {}
        self.labels = {}
        self.fields = {}
        self.explicit_overrides = {}

        required = self._required_blocked(result)
        for missing in required:
            identifier = missing["field_id"]
            field = missing["field"]
            self.values[identifier] = previous_values.get(identifier, "")
            self.labels[identifier] = missing["label"]
            self.fields[identifier] = field

            row = self._table_row_for_field_id(identifier)
            if row is None:
                continue

            editor = QLineEdit()
            editor.setPlaceholderText("必填 · AI 未 READY，请明确填写或确认")
            if self.values[identifier]:
                editor.setText(self.values[identifier])
            options = missing.get("options") or []
            tooltip = missing.get("reason") or "AI 未将该必填字段判断为 READY。"
            tooltip += "\n\n程序不会自动生成兜底值；需要明确用户输入后才能执行 Full Step 3。"
            if options:
                tooltip += "\n\nMakro 当前可见选项：\n" + " | ".join(options)
            editor.setToolTip(tooltip)
            editor.textChanged.connect(
                lambda text, fid=identifier: self._input_changed(fid, text)
            )
            self.window.field_table.setCellWidget(row, 2, editor)
            self.inputs[identifier] = editor

        if required:
            self.window.fields_hint.setText(
                f"READY={result.ready} · {len(required)} 个 Makro 必填项等待明确确认"
            )
            self.window.real_policy_hint.setText(
                f"还有 {len(required)} 个 Makro 必填项不是 AI READY。程序不会代替 AI 猜值或机械兜底；"
                "请明确填写/确认后再执行 Full Step 3。"
            )
        self._sync_button()

    def _input_changed(self, field_id: str, text: str) -> None:
        if field_id not in self.fields:
            return
        self.values[field_id] = str(text or "")
        self.explicit_overrides.pop(field_id, None)
        self._sync_button()

    def set_explicit_override(
        self,
        field_id: str,
        values: list[str] | tuple[str, ...],
        *,
        qualifier: str = "",
        reason: str = "Explicit Product Pack conflict alternative confirmed by the user.",
    ) -> bool:
        """Install one exact user-confirmed alternative for an unresolved field."""

        identifier = str(field_id or "").strip()
        field = self.fields.get(identifier)
        if field is None:
            return False
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        if not cleaned:
            return False

        display = " + ".join(cleaned)
        if qualifier.strip():
            display = f"{display} {qualifier.strip()}".strip()
        editor = self.inputs.get(identifier)
        if editor is not None:
            editor.setText(display)
        self.values[identifier] = display
        self.explicit_overrides[identifier] = {
            **required_override_binding(field),
            "values": cleaned,
            "qualifier": str(qualifier or "").strip(),
            "source_type": "user",
            "reason": str(reason or "").strip(),
        }
        self._sync_button()
        return True

    def _manual_count(self) -> int:
        return sum(
            bool(self.explicit_overrides.get(identifier))
            or bool(self.values.get(identifier, "").strip())
            for identifier in self.fields
        )

    def _all_required_confirmed(self) -> bool:
        return self._manual_count() == len(self.fields)

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
            confirmed = self._manual_count()
            total = len(self.fields)
            self.window.real_start_button.setEnabled(
                result.ready > 0 and confirmed == total
            )
            self.window.real_start_button.setToolTip(
                f"AI 未 READY 的必填项已明确确认 {confirmed}/{total}；程序不会自动补值。"
            )
            return

        self.window.real_start_button.setEnabled(result.ready > 0)
        if result.ready <= 0:
            self.window.real_start_button.setToolTip("当前 Fill Plan 没有 AI READY 字段。")

    def _merged_overrides(self) -> list[dict[str, Any]]:
        """Return explicit user decisions only; never synthesize fallback values."""

        overrides: list[dict[str, Any]] = []
        for identifier, field in self.fields.items():
            explicit = self.explicit_overrides.get(identifier)
            if explicit is not None:
                overrides.append(dict(explicit))
                continue
            value = self.values.get(identifier, "").strip()
            if value:
                overrides.append(
                    {
                        **required_override_binding(field),
                        "values": [value],
                        "source_type": "user",
                    }
                )
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
        """Start execution only after every non-READY required field is explicit."""

        result = getattr(self.window, "current_result", None)
        if self.window.runner.is_running or self.window.execution_runner.is_running:
            QMessageBox.warning(self.window, "无法开始真实填写", "当前已有准备流程或真实执行正在运行。")
            return
        if result is None or not result.plan_summary:
            QMessageBox.warning(self.window, "无法开始真实填写", "请先完成 Step 3 Resolver + Fill Plan。")
            return
        if result.ready <= 0:
            QMessageBox.warning(self.window, "没有可填写字段", "当前 Fill Plan 没有 AI READY 字段，真实填写保持锁定。")
            return

        scope = self.window.real_scope_combo.currentData()
        try:
            if scope == FULL_STEP3:
                if self.fields and not self._all_required_confirmed():
                    QMessageBox.warning(
                        self.window,
                        "仍有未确认必填项",
                        "AI 未 READY 的必填字段必须由你明确填写或确认；程序不会自动生成 N/A、1 或首个选项。",
                    )
                    return
                path = self._write_overrides()
                if self.fields:
                    manual = self._manual_count()
                    self.window.fields_hint.setText(
                        f"必填预检完成 · 明确用户确认 {manual}/{len(self.fields)} · 自动兜底 0"
                    )
                    self.window.real_policy_hint.setText(
                        "Full Step 3 只执行 AI READY 与明确用户确认值；Python 不再补值或重新解释商品语义。"
                    )
                    append = getattr(self.window, "_append_log", None)
                    if callable(append):
                        append(
                            f"[required-preflight] overrides={path or 'none'} "
                            f"user={manual} fallback_candidates=0 ai_calls=0"
                        )
            else:
                schema_path = latest_live_schema(result.run_dir)
                if schema_path is not None:
                    stale = schema_path.with_name("required-overrides.json")
                    if stale.exists():
                        stale.unlink()
        except Exception as exc:
            QMessageBox.critical(self.window, "必填字段确认失败", str(exc))
            return

        self._original_start()

    def _on_start_clicked(self, _checked: bool = False) -> None:
        self.request_start(_checked)


def install_required_input_support(window: Any) -> RequiredInputSupport:
    support = RequiredInputSupport(window)
    window._required_input_support = support
    return support
