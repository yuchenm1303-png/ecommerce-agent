from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QLineEdit, QMessageBox

from app.business_decisions import (
    is_user_decision_business_field,
    price_decision_advisory,
)
from app.listing_content_policy import allow_required_fallback
from app.required_overrides import (
    load_required_blocked_fields,
    required_fallback_override,
    required_override_binding,
)
from .real_execution import FULL_STEP3
from .result_loader import RunResult, latest_fill_plan, latest_live_schema


class RequiredInputSupport(QObject):
    """Cover required Makro gaps after the authoritative AI pass.

    AI READY fields are never changed here.  Only required fields that remain
    BLOCKED after Resolver are exposed for optional manual input; if the user
    leaves one empty, Full Step 3 generates the shared deterministic live-schema
    fallback (first executable option, numeric ``1`` with live qualifier, or
    free-text ``N/A``).

    This is a final execution fallback, not a second product-decision layer and
    not a second AI call.  The canonical executor still rebinds the current DOM
    and validates fallback/user values mechanically before any browser write.

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

    def _fallback_preview(self, field: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        try:
            fallback = required_fallback_override(field)
        except Exception:
            return None, "无法生成自动兜底"

        values = [
            str(value).strip()
            for value in fallback.get("values") or []
            if str(value).strip()
        ]
        display = " + ".join(values) or "N/A"
        qualifier = str(fallback.get("qualifier") or "").strip()
        if qualifier:
            display = f"{display} {qualifier}".strip()
        return fallback, display

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

            fallback, fallback_text = self._fallback_preview(field)
            advisory = price_decision_advisory(field)
            editor = QLineEdit()
            if advisory:
                editor.setPlaceholderText("经营决策 · 请手动确认（不会使用占位价格）")
                editor.setProperty("sellerDecisionKind", advisory.get("kind", "user_decision"))
            elif fallback is None:
                editor.setPlaceholderText("必填 · 当前 live 字段无法自动兜底")
            else:
                editor.setPlaceholderText(f"必填 · 留空将自动填 {fallback_text}")
            if self.values[identifier]:
                editor.setText(self.values[identifier])

            options = missing.get("options") or []
            tooltip = missing.get("reason") or "AI 未将该必填字段判断为 READY。"
            if advisory:
                rows = "\n".join(
                    f"{row.get('label', '')}: {row.get('value', '')}"
                    for row in advisory.get("rows") or []
                )
                tooltip += (
                    "\n\n这是卖家经营决策，程序不会替你决定，也不会恢复旧的临时占位价。"
                    f"\n{rows}"
                    f"\n\n{advisory.get('thought', '')}"
                )
            elif fallback is None:
                tooltip += "\n\n当前 live 字段无法生成合法自动兜底；可手动提供值后继续。"
            else:
                tooltip += (
                    "\n\n无需再次运行 AI。"
                    f"Full Step 3 开始前若仍留空，将自动填写：{fallback_text}。"
                )
            if options:
                tooltip += "\n\nMakro 当前可见选项：\n" + " | ".join(options)
            editor.setToolTip(tooltip)
            editor.textChanged.connect(
                lambda text, fid=identifier: self._input_changed(fid, text)
            )
            self.window.field_table.setCellWidget(row, 2, editor)
            self.inputs[identifier] = editor

        if required:
            decisions = sum(
                1 for item in required if is_user_decision_business_field(item["field"])
            )
            fallback_count = len(required) - decisions
            self.window.fields_hint.setText(
                f"READY={result.ready} · {decisions} 个经营决策待确认"
                + (f" · {fallback_count} 个普通必填可兜底" if fallback_count else "")
            )
            self.window.real_policy_hint.setText(
                "价格等经营决策必须由你明确确认，程序不会使用临时占位价；"
                + (
                    f"其余 {fallback_count} 个普通 required 字段仍保留现有机械兜底。"
                    if fallback_count
                    else "当前没有需要机械兜底的普通 required 字段。"
                )
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

    def _missing_user_decisions(self) -> list[str]:
        return [
            self.labels.get(identifier, identifier)
            for identifier, field in self.fields.items()
            if is_user_decision_business_field(field)
            and not bool(self.explicit_overrides.get(identifier))
            and not bool(self.values.get(identifier, "").strip())
        ]

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
        if scope == FULL_STEP3:
            missing_decisions = self._missing_user_decisions()
            if missing_decisions:
                self.window.real_start_button.setEnabled(False)
                self.window.real_start_button.setToolTip(
                    "请先确认经营决策字段：" + "、".join(missing_decisions)
                )
                return
        if scope == FULL_STEP3 and self.fields:
            self.window.real_start_button.setEnabled(result.ready > 0 or bool(self.fields))
            manual = self._manual_count()
            automatic = len(self.fields) - manual
            self.window.real_start_button.setToolTip(
                f"可直接开始；{manual} 个使用用户值，其余 {automatic} 个未解决必填项自动兜底。"
            )
            return

        self.window.real_start_button.setEnabled(result.ready > 0)
        if result.ready <= 0:
            self.window.real_start_button.setToolTip("当前 Fill Plan 没有 READY 字段。")

    def _merged_overrides(self) -> list[dict[str, Any]]:
        """User value wins; otherwise generate fallback for every blocked required field."""

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
                if not allow_required_fallback(field):
                    label = self.labels.get(identifier, identifier)
                    raise RuntimeError(
                        f"{label} 是卖家经营决策，必须由用户明确确认；不会自动使用占位值。"
                    )
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
        """Run canonical preflight and automatically cover unresolved required gaps."""

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
                        f"必填预检完成 · 用户填写 {manual} · 自动兜底 {automatic}"
                    )
                    self.window.real_policy_hint.setText(
                        "AI READY 仍然原样执行；只有 AI 未解决的必填项才使用自动兜底，"
                        "并在当前 Makro DOM 上做机械校验后填写。"
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
