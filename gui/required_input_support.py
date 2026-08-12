from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QLineEdit, QMessageBox

from app.ai_decisions import field_id
from app.required_overrides import required_override_binding
from .real_execution import FULL_STEP3
from .result_loader import RunResult, latest_fill_plan, latest_live_schema


class RequiredInputSupport(QObject):
    """Require explicit user values for unresolved Makro required fields.

    The normal Resolver/Web pipeline remains authoritative. Required fields that
    are still BLOCKED after that pipeline stay visible in the field table and are
    never sent through a second AI/search pass. They also never receive synthetic
    ``N/A``/``1``/first-option placeholders in the formal GUI.

    Full Step 3 remains locked until every unresolved required field has an
    explicit user value and Product Photos has been explicitly authorized. User
    values are persisted only as per-run overrides, rebound to the current live
    schema, and still pass the existing mechanical option/unit/hard-field guards
    before any browser write.
    """

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.inputs: dict[str, QLineEdit] = {}
        self.labels: dict[str, str] = {}
        self.fields: dict[str, dict[str, Any]] = {}
        self._original_start = window._start_real_execution

        window.runner.result_updated.connect(self._on_result)
        window.runner.completed.connect(lambda _result: self._sync_button())
        window.execution_runner.running_changed.connect(lambda _running: self._sync_button())
        window.real_scope_combo.currentIndexChanged.connect(lambda _index: self._sync_button())
        window.real_upload_check.toggled.connect(lambda _checked: self._sync_button())

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
        fields = (
            raw_schema.get("fields") or raw_schema.get("items") or []
            if isinstance(raw_schema, dict)
            else raw_schema
        )
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
                    "field": field,
                    "label": str(item.get("label") or item.get("attribute_key") or "必填字段"),
                    "reason": str(item.get("reason") or resolution.get("detail") or "").strip(),
                    "options": [
                        str(value).strip()
                        for value in resolution.get("question_options") or []
                        if str(value).strip()
                        and str(value).strip().casefold() not in {"select one", "select"}
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
        self.fields = {}

        required = self._required_blocked(result)
        for missing in required:
            identifier = missing["field_id"]
            row = self._table_row_for_field_id(identifier)
            if row is None:
                continue

            field = missing["field"]
            editor = QLineEdit()
            editor.setPlaceholderText("必填 · 请填写真实值")
            if previous.get(identifier):
                editor.setText(previous[identifier])
            options = missing.get("options") or []
            tooltip = missing.get("reason") or "正常 Resolver/Web 未能可靠确定该必填字段。"
            tooltip += (
                "\n\n不会再次调用 AI，也不会自动写入 N/A、1 或第一个下拉选项。"
                "Full Step 3 开始前必须由你明确填写真实值。"
            )
            if options:
                tooltip += "\n\nMakro 当前可选值：\n" + " | ".join(options)
            editor.setToolTip(tooltip)
            editor.textChanged.connect(lambda _text, fid=identifier: self._input_changed(fid))
            self.window.field_table.setCellWidget(row, 2, editor)
            self.inputs[identifier] = editor
            self.labels[identifier] = missing["label"]
            self.fields[identifier] = field

        if required:
            self.window.fields_hint.setText(
                f"READY={result.ready} · {len(required)} 个必填缺口需要显式补充"
            )
            self.window.real_policy_hint.setText(
                f"还有 {len(required)} 个 Makro 必填项未由正常 Resolver/Web 可靠确定。"
                "请直接在字段表中填写真实值；全部补齐前 Full Step 3 保持锁定。"
                "不会再次调用 AI，也不会使用固定兜底值。"
            )
        self._sync_button()

    def _input_changed(self, _field_id: str) -> None:
        self._sync_button()

    def _missing_input_ids(self) -> list[str]:
        return [
            identifier
            for identifier, editor in self.inputs.items()
            if not editor.text().strip()
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
            photos_authorized = self.window.real_upload_check.isChecked()
            missing = self._missing_input_ids()
            enabled = photos_authorized and not missing and (result.ready > 0 or bool(self.inputs))
            self.window.real_start_button.setEnabled(enabled)
            if not photos_authorized:
                self.window.real_start_button.setToolTip(
                    "Full Step 3 是完整 draft persistence 验收，必须显式勾选“上传本次商品图”。"
                )
            elif missing:
                names = [self.labels.get(identifier, identifier) for identifier in missing]
                self.window.real_start_button.setToolTip(
                    "Full Step 3 仍缺少必填真实值：" + " | ".join(names)
                )
            elif result.ready <= 0 and not self.inputs:
                self.window.real_start_button.setToolTip("当前 Fill Plan 没有 READY 字段。")
            else:
                self.window.real_start_button.setToolTip(
                    "required 输入与 Product Photos 授权已齐全；可以进入真实写入预检。"
                )
            return

        self.window.real_start_button.setEnabled(result.ready > 0)
        if result.ready <= 0:
            self.window.real_start_button.setToolTip("当前 Fill Plan 没有 READY 字段。")

    def _merged_overrides(self) -> list[dict[str, Any]]:
        overrides: list[dict[str, Any]] = []
        missing: list[str] = []
        for identifier, editor in self.inputs.items():
            value = editor.text().strip()
            field = self.fields.get(identifier)
            if not value:
                missing.append(self.labels.get(identifier, identifier))
                continue
            if field is None:
                raise RuntimeError(f"必填字段绑定信息已失效：{identifier}")
            overrides.append(
                {
                    **required_override_binding(field),
                    "values": [value],
                    "source_type": "user",
                }
            )
        if missing:
            raise RuntimeError("仍有必填字段未填写：" + " | ".join(missing))
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
        """Run canonical preflight after every production acceptance gate is explicit."""

        result = getattr(self.window, "current_result", None)
        if self.window.runner.is_running or self.window.execution_runner.is_running:
            QMessageBox.warning(self.window, "无法开始真实填写", "当前已有准备流程或真实执行正在运行。")
            return
        if result is None or not result.plan_summary:
            QMessageBox.warning(self.window, "无法开始真实填写", "请先完成 Step 3 Resolver + Fill Plan。")
            return
        if result.ready <= 0 and not self.inputs:
            QMessageBox.warning(self.window, "没有可填写字段", "当前 Fill Plan 没有 READY 字段，真实填写保持锁定。")
            return

        scope = self.window.real_scope_combo.currentData()
        try:
            if scope == FULL_STEP3:
                if not self.window.real_upload_check.isChecked():
                    QMessageBox.warning(
                        self.window,
                        "Full Step 3 需要图片授权",
                        "完整 draft persistence 验收包含 Product Photos。"
                        "请显式勾选“上传本次商品图”；系统会优先复用本次 Resolver 抓取的真实商品图。",
                    )
                    return
                missing = self._missing_input_ids()
                if missing:
                    names = [self.labels.get(identifier, identifier) for identifier in missing]
                    QMessageBox.warning(
                        self.window,
                        "必填字段尚未补齐",
                        "Full Step 3 不会编造 required 值。请先填写：\n" + "\n".join(names),
                    )
                    return
                path = self._write_overrides()
                if self.inputs:
                    count = len(self.inputs)
                    self.window.fields_hint.setText(f"必填预检完成 · 显式用户值 {count}")
                    self.window.real_policy_hint.setText(
                        "Full Step 3 将继续使用你明确填写的 required 值和已授权的 Product Photos。"
                        "执行器仍会在浏览器写入前校验当前 Makro option / unit / hard-field 约束。"
                    )
                    append = getattr(self.window, "_append_log", None)
                    if callable(append):
                        append(
                            f"[required-user-input] overrides={path or 'none'} "
                            f"manual={count} ai_calls=0 fallback=0 photos_authorized=true"
                        )
            else:
                schema_path = latest_live_schema(result.run_dir)
                if schema_path is not None:
                    stale = schema_path.with_name("required-overrides.json")
                    if stale.exists():
                        stale.unlink()
        except Exception as exc:
            QMessageBox.critical(self.window, "无法保存必填补充值", str(exc))
            return

        self._original_start()

    def _on_start_clicked(self, _checked: bool = False) -> None:
        self.request_start(_checked)


def install_required_input_support(window: Any) -> RequiredInputSupport:
    support = RequiredInputSupport(window)
    window._required_input_support = support
    return support