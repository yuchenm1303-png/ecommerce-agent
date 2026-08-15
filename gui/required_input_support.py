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
    deterministic fallback derived from the current live schema. Content-policy
    rules still make the Resolver prefer grounded facts/synthesis and avoid
    placeholders as normal AI answers; they do not become a late GUI execution
    lock. A Product Pack conflict review may also install one exact AI alternative
    here as an explicit user confirmation, and every value still goes through the
    same executor-side live option/unit validation before any browser write.

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
        # Exact structured user confirmations (values + optional qualifier).
        # This is intentionally separate from the text editor model so a value
        # such as 10 + cm is not flattened into the invalid literal "10 cm".
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
        # Preserve user-entered free text from the Python model, never from old
        # QLineEdit wrappers: QTableWidget may already have deleted those C++
        # objects while rebuilding the result table. Structured confirmations do
        # not cross result boundaries; a fresh Resolver result must be reviewed
        # again instead of inheriting a previous product's choice.
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

            # Required fallback is business state and must exist even if the
            # corresponding table row is temporarily not rendered/filtered.
            self.values[identifier] = previous_values.get(identifier, "")
            self.labels[identifier] = missing["label"]
            self.fields[identifier] = field

            row = self._table_row_for_field_id(identifier)
            if row is None:
                continue

            fallback_text = "无法生成兜底"
            try:
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
            except Exception:
                # Only a genuinely unusable live-field contract should reach
                # this branch; content-quality preferences never disable fallback.
                fallback = None

            editor = QLineEdit()
            if fallback is None:
                editor.setPlaceholderText("必填 · 当前 live 字段无法自动兜底")
            else:
                editor.setPlaceholderText(f"必填 · 留空将自动填 {fallback_text}")
            if self.values[identifier]:
                editor.setText(self.values[identifier])
            options = missing.get("options") or []
            tooltip = missing.get("reason") or "正常 Resolver 未能确定该必填字段。"
            if fallback is None:
                tooltip += "\n\n当前 live 字段无法生成合法机械兜底，可手动提供值后继续。"
            else:
                tooltip += (
                    "\n\n无需再次运行 AI。"
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
                f"READY={result.ready} · {len(required)} 个 Makro 必填缺口可确认 / 自动兜底"
            )
            self.window.real_policy_hint.setText(
                f"还有 {len(required)} 个 Makro 必填项未由正常 Resolver 确定。可手动提供准确值；"
                "留空则在 Full Step 3 最后阶段使用固定非 AI 兜底，不会因为内容质量策略阻塞正常上架。"
            )
        self._sync_button()

    def _input_changed(self, field_id: str, text: str) -> None:
        # A stale editor can emit during a table rebuild. Ignore it once its
        # field no longer belongs to the current result model.
        if field_id not in self.fields:
            return
        self.values[field_id] = str(text or "")
        # Manual editing deliberately replaces an earlier structured conflict
        # confirmation for the same field.
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
        """Install one exact user-confirmed Resolver alternative for a required field.

        Returns False when the field is not one of the current unresolved required
        fields. No browser write happens here; the canonical executor revalidates
        the value against the current live control immediately before writing.
        """

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
            # textChanged may clear an old explicit override; install the new
            # structured record after the display editor has been synchronized.
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
                f"可直接开始预检；{manual} 个使用用户确认值，其余 {automatic} 个未解决必填项使用固定机械兜底。"
            )
            return

        self.window.real_start_button.setEnabled(result.ready > 0)
        if result.ready <= 0:
            self.window.real_start_button.setToolTip("当前 Fill Plan 没有 READY 字段。")

    def _merged_overrides(self) -> list[dict[str, Any]]:
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
            else:
                # Resolver has already had its chance. Any remaining required
                # field now receives the shared deterministic live-schema fallback.
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
        """Run canonical preflight and cover every unresolved required gap."""

        result = getattr(self.window, "current_result", None)
        if self.window.runner.is_running or self.window.execution_runner.is_running:
            QMessageBox.warning(self.window, "无法开始真实填写", "当前已有准备流程或真实执行正在运行。")
            return
        if result is None or not result.plan_summary:
            QMessageBox.warning(self.window, "无法开始真实填写", "请先完成 Step 3 Resolver + Fill Plan。")
            return
        if result.ready <= 0 and not self.fields:
            QMessageBox.warning(self.window, "没有可填写字段", "当前 Fill Plan 没有 READY 或待确认的必填字段，真实填写保持锁定。")
            return

        scope = self.window.real_scope_combo.currentData()
        try:
            if scope == FULL_STEP3:
                path = self._write_overrides()
                if self.fields:
                    manual = self._manual_count()
                    automatic = len(self.fields) - manual
                    self.window.fields_hint.setText(
                        f"必填预检完成 · 用户确认 {manual} · 自动兜底 {automatic}"
                    )
                    self.window.real_policy_hint.setText(
                        "Full Step 3 将继续进入 canonical executor。用户确认值与固定机械兜底都会在当前 Makro DOM 上重新校验；"
                        "内容生成规则只负责优先得到更好的答案，不会在这里阻塞任务。"
                    )
                    append = getattr(self.window, "_append_log", None)
                    if callable(append):
                        append(
                            f"[required-preflight] overrides={path or 'none'} "
                            f"user={manual} fallback_candidates={automatic} ai_calls=0"
                        )
            else:
                schema_path = latest_live_schema(result.run_dir)
                if schema_path is not None:
                    stale = schema_path.with_name("required-overrides.json")
                    if stale.exists():
                        stale.unlink()
        except Exception as exc:
            QMessageBox.critical(self.window, "必填字段兜底失败", str(exc))
            return

        self._original_start()

    def _on_start_clicked(self, _checked: bool = False) -> None:
        self.request_start(_checked)


def install_required_input_support(window: Any) -> RequiredInputSupport:
    support = RequiredInputSupport(window)
    window._required_input_support = support
    return support
