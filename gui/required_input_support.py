from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer
from PySide6.QtWidgets import QLineEdit, QMessageBox

from app.ai_decisions import field_id
from .real_execution import FULL_STEP3
from .result_loader import (
    RunResult,
    latest_fill_plan,
    latest_live_schema,
    latest_resolver_manifest,
)


class RequiredInputSupport(QObject):
    """Required-field preflight with targeted AI completion and manual fallback.

    The normal Resolver remains authoritative. Required fields still BLOCKED
    after that pass stay visible in the field table, but they no longer disable
    Full Step 3 before the user can even start. On the first Full Step 3 request
    the GUI runs one isolated AI completion pass using the current Resolver
    evidence. Values that pass the live Makro option/unit guards are persisted
    as model-origin required overrides. Only residual unresolved fields require
    manual text. Manual text always wins over an AI completion for the same
    field. No placeholder text is ever persisted to Makro.
    """

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.inputs: dict[str, QLineEdit] = {}
        self.labels: dict[str, str] = {}
        self._original_start = window._start_real_execution
        self._auto_overrides: dict[str, dict[str, Any]] = {}
        self._auto_attempted = False
        self._auto_process: QProcess | None = None
        self._auto_output_path: Path | None = None
        self._auto_log_tail = ""

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
        fields = (raw_schema.get("fields") or raw_schema.get("items") or []) if isinstance(raw_schema, dict) else raw_schema
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
        self._auto_overrides = {}
        self._auto_attempted = False
        self._auto_output_path = None
        required = self._required_blocked(result)
        for missing in required:
            identifier = missing["field_id"]
            row = self._table_row_for_field_id(identifier)
            if row is None:
                continue
            editor = QLineEdit()
            editor.setPlaceholderText("必填 · 留空即可在真实填写前由 AI 自动补齐")
            if previous.get(identifier):
                editor.setText(previous[identifier])
            options = missing.get("options") or []
            tooltip = missing.get("reason") or "正常 Resolver 未能确定该必填字段。"
            tooltip += "\n\n点击 Full Step 3 时会先运行一次专门的 AI 必填补齐；你也可以在这里手动覆盖。"
            if options:
                tooltip += "\n\nMakro 可选值：\n" + " | ".join(options)
            editor.setToolTip(tooltip)
            editor.textChanged.connect(lambda _text, fid=identifier: self._input_changed(fid))
            self.window.field_table.setCellWidget(row, 2, editor)
            self.inputs[identifier] = editor
            self.labels[identifier] = missing["label"]
        if required:
            self.window.fields_hint.setText(f"READY={result.ready} · {len(required)} 个必填缺口将在真实填写前自动补齐")
            self.window.real_policy_hint.setText(
                f"还有 {len(required)} 个 Makro 必填项未由正常 Resolver 确定。现在可以直接开始 Full Step 3："
                "系统会先用当前商品证据做一次定向 AI 补齐，通过 Makro hard guard 后自动继续；"
                "只有仍无法可靠确定的字段才需要手动填写。"
            )
        self._sync_button()

    def _input_changed(self, _field_id: str) -> None:
        self._sync_button()

    def _manual_value(self, identifier: str) -> str:
        editor = self.inputs.get(identifier)
        return editor.text().strip() if editor is not None else ""

    def _all_required_covered(self) -> bool:
        return all(self._manual_value(identifier) or identifier in self._auto_overrides for identifier in self.inputs)

    def _uncovered_ids(self) -> list[str]:
        return [identifier for identifier in self.inputs if not self._manual_value(identifier) and identifier not in self._auto_overrides]

    def _sync_button(self) -> None:
        result = getattr(self.window, "current_result", None)
        if self._auto_process is not None and self._auto_process.state() != QProcess.NotRunning:
            self.window.real_start_button.setEnabled(False)
            self.window.real_start_button.setToolTip("正在用当前商品证据自动补齐 Makro 必填字段。")
            return
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
            can_start = result.ready > 0 or bool(self.inputs)
            self.window.real_start_button.setEnabled(can_start)
            uncovered = self._uncovered_ids()
            if not self._auto_attempted:
                self.window.real_start_button.setToolTip(f"可直接开始；执行前 AI 会自动尝试补齐 {len(uncovered)} 个必填缺口。")
            elif uncovered:
                self.window.real_start_button.setToolTip(f"AI 已完成一次必填补齐；仍有 {len(uncovered)} 个字段需要手动确认。")
            else:
                self.window.real_start_button.setToolTip("必填项已由 AI/用户补齐；将执行全部 READY。")
        else:
            self.window.real_start_button.setEnabled(result.ready > 0)
            if result.ready <= 0:
                self.window.real_start_button.setToolTip("当前 Fill Plan 没有 READY 字段。")

    def _merged_overrides(self) -> list[dict[str, Any]]:
        merged = {identifier: dict(payload) for identifier, payload in self._auto_overrides.items() if identifier in self.inputs}
        for identifier, editor in self.inputs.items():
            value = editor.text().strip()
            if value:
                merged[identifier] = {"field_id": identifier, "values": [value], "source_type": "user"}
        return list(merged.values())

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
        path.write_text(json.dumps({"overrides": overrides}, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _start_auto_completion(self, result: RunResult) -> None:
        plan_path = latest_fill_plan(result.run_dir)
        schema_path = latest_live_schema(result.run_dir)
        resolver_manifest = latest_resolver_manifest(result.run_dir, "03-hot-resolver")
        if plan_path is None or schema_path is None or resolver_manifest is None or not plan_path.is_file() or not schema_path.is_file() or not resolver_manifest.is_file():
            self._auto_attempted = True
            QMessageBox.warning(self.window, "无法自动补齐必填字段", "当前 run 的 Fill Plan / live schema / Resolver manifest 不完整；你仍可在字段表中手动填写剩余必填项。")
            self._sync_button()
            return
        output = schema_path.with_name("required-auto-completion.json")
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        self._auto_output_path = output
        self._auto_log_tail = ""
        process = QProcess(self)
        self._auto_process = process
        process.setWorkingDirectory(str(self.window.project_root))
        process.setProcessChannelMode(QProcess.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self._read_auto_output)
        process.finished.connect(self._auto_finished)
        args = [
            "makro_complete_required.py",
            "--fill-plan", str(plan_path),
            "--live-schema", str(schema_path),
            "--resolver-manifest", str(resolver_manifest),
            "--output", str(output),
        ]
        self.window.real_start_button.setEnabled(False)
        self.window.real_start_button.setText("AI 补齐必填项…")
        self.window.fields_hint.setText(f"正在定向补齐 {len(self.inputs)} 个 Makro 必填缺口…")
        self.window.real_policy_hint.setText("Full Step 3 尚未开始写入。正在使用本次 Resolver 的商品证据做一次必填字段 AI 补齐；结果仍会经过当前 Makro option / unit hard guard。")
        self.window.phase_badge.setText("REQUIRED AI · completing required fields")
        process.start(sys.executable, args)

    def _read_auto_output(self) -> None:
        process = self._auto_process
        if process is None:
            return
        raw = bytes(process.readAllStandardOutput())
        if not raw:
            return
        text = self._auto_log_tail + raw.decode("utf-8", errors="replace")
        self._auto_log_tail = ""
        for part in text.splitlines(keepends=True):
            if part.endswith(("\n", "\r")):
                line = part.rstrip("\r\n")
                if line:
                    append = getattr(self.window, "_append_log", None)
                    if callable(append):
                        append("[required-ai] " + line)
            else:
                self._auto_log_tail = part

    def _restore_start_button_text(self) -> None:
        sync = getattr(self.window, "_sync_execution_mode_copy", None)
        if callable(sync):
            sync()
        else:
            self.window.real_start_button.setText("一键填写全部 READY")

    def _auto_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._read_auto_output()
        self._auto_process = None
        self._auto_attempted = True
        self._restore_start_button_text()
        if exit_code != 0 or self._auto_output_path is None or not self._auto_output_path.is_file():
            self.window.phase_badge.setText("REQUIRED AI · failed · manual fallback available")
            self.window.real_policy_hint.setText("必填字段 AI 补齐未完成；真实 Makro 写入尚未开始。你可以直接在字段表中手动填写剩余必填项后重试。")
            QMessageBox.warning(self.window, "AI 必填补齐未完成", "定向 AI 补齐没有产生可用结果；没有进行任何 Makro 写入。请查看日志，或手动填写仍缺失的必填字段。")
            self._sync_button()
            return
        try:
            payload = json.loads(self._auto_output_path.read_text(encoding="utf-8"))
            raw_overrides = payload.get("overrides") if isinstance(payload, dict) else []
            overrides = [item for item in raw_overrides or [] if isinstance(item, dict)]
        except Exception as exc:
            self.window.phase_badge.setText("REQUIRED AI · invalid result")
            QMessageBox.warning(self.window, "AI 必填结果无效", str(exc))
            self._sync_button()
            return
        self._auto_overrides = {
            str(item.get("field_id") or ""): item
            for item in overrides
            if str(item.get("field_id") or "") in self.inputs
        }
        for identifier, override in self._auto_overrides.items():
            editor = self.inputs.get(identifier)
            if editor is None or editor.text().strip():
                continue
            values = [str(value).strip() for value in override.get("values") or [] if str(value).strip()]
            rendered = " + ".join(values)
            editor.setPlaceholderText(f"AI 自动补齐 · {rendered}" if rendered else "AI 已补齐")
            editor.setToolTip(editor.toolTip() + "\n\nAI 自动补齐候选：" + (rendered or "—") + "\n你仍可输入手动值覆盖它。")
        uncovered = self._uncovered_ids()
        auto_count = len(self._auto_overrides)
        if uncovered:
            labels = [self.labels.get(identifier, identifier) for identifier in uncovered]
            self.window.fields_hint.setText(f"AI 已自动补齐 {auto_count} 个；还剩 {len(uncovered)} 个必填项需要确认")
            self.window.real_policy_hint.setText("AI 已完成一次定向必填补齐。只有以下字段仍无法可靠确定，请手动填写后再继续：" + " | ".join(labels))
            self.window.phase_badge.setText(f"REQUIRED AI · {auto_count} completed · {len(uncovered)} manual")
            self._sync_button()
            return
        try:
            self._write_overrides()
        except Exception as exc:
            QMessageBox.critical(self.window, "无法保存 AI 必填补齐结果", str(exc))
            self._sync_button()
            return
        self.window.fields_hint.setText(f"必填项已补齐 · AI {auto_count} · 即将开始真实填写")
        self.window.real_policy_hint.setText("必填字段已通过 AI/用户补齐并写入本次 run 的受控 override；现在继续原有 Full Step 3 确认与真实填写流程。")
        self.window.phase_badge.setText("REQUIRED AI · complete · ready for real execution")
        self._sync_button()
        QTimer.singleShot(0, self._original_start)

    def request_start(self, _checked: bool = False) -> None:
        """Run canonical preflight; auto-complete required gaps before any write."""
        result = getattr(self.window, "current_result", None)
        if self.window.runner.is_running or self.window.execution_runner.is_running:
            QMessageBox.warning(self.window, "无法开始真实填写", "当前已有准备流程或真实执行正在运行。")
            return
        if self._auto_process is not None and self._auto_process.state() != QProcess.NotRunning:
            return
        if result is None or not result.plan_summary:
            QMessageBox.warning(self.window, "无法开始真实填写", "请先完成 Step 3 Resolver + Fill Plan。")
            return
        scope = self.window.real_scope_combo.currentData()
        if scope == FULL_STEP3 and self.inputs and not self._all_required_covered():
            if not self._auto_attempted:
                self._start_auto_completion(result)
                return
            missing = [self.labels.get(identifier, identifier) for identifier in self._uncovered_ids()]
            QMessageBox.warning(self.window, "仍有少量必填项需要确认", "AI 已自动尝试补齐；以下字段仍无法可靠确定，请手动填写：\n" + "\n".join(f"• {label}" for label in missing))
            return
        if result.ready <= 0 and not self._merged_overrides():
            QMessageBox.warning(self.window, "没有可填写字段", "当前 Fill Plan 没有 READY 字段，且必填补齐也没有产生可执行值。")
            return
        try:
            self._write_overrides()
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
