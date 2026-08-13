from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MethodType
from typing import Any

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.listing_content_policy import (
    LISTING_INTENT_ENV,
    allow_required_fallback,
)
from app.required_overrides import (
    load_required_blocked_fields,
    load_required_overrides,
    required_fallback_override,
    required_override_binding,
)
from app.source_bundle import normalize_key
from .real_execution import FULL_STEP3
from .result_loader import latest_fill_plan, latest_live_schema, latest_resolver_manifest


_INTENT_SIDECAR = "listing-intent.json"
_INTENT_LIMIT = 600


def _clean_intent(value: object) -> str:
    return " ".join(str(value or "").split()).strip()[:_INTENT_LIMIT]


def _write_intent_sidecar(root: Path, intent: str) -> None:
    target = root.resolve() / _INTENT_SIDECAR
    payload = {
        "listing_intent": _clean_intent(intent),
        "kind": "listing_offer_scope",
        "source": "user",
        "note": "Per-listing sold variant/bundle scope; not the Makro seller SKU identifier.",
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _with_process_intent(intent: str, callback: Any) -> Any:
    """Run a synchronous process-spawn callback with one process-local intent.

    QProcessEnvironment.systemEnvironment() snapshots the environment inside the
    existing runner/controller spawn path, so restoring immediately afterwards is
    safe and prevents one Batch job's selected offer leaking into another job.
    """

    cleaned = _clean_intent(intent)
    sentinel = object()
    previous: object = os.environ.get(LISTING_INTENT_ENV, sentinel)  # type: ignore[assignment]
    try:
        if cleaned:
            os.environ[LISTING_INTENT_ENV] = cleaned
        else:
            os.environ.pop(LISTING_INTENT_ENV, None)
        return callback()
    finally:
        if previous is sentinel:
            os.environ.pop(LISTING_INTENT_ENV, None)
        else:
            os.environ[LISTING_INTENT_ENV] = str(previous)


def _protected_required(run_dir: Path) -> list[dict[str, Any]]:
    plan_path = latest_fill_plan(run_dir)
    schema_path = latest_live_schema(run_dir)
    if plan_path is None or schema_path is None or not plan_path.is_file() or not schema_path.is_file():
        return []
    return [
        item
        for item in load_required_blocked_fields(plan_path, schema_path)
        if not allow_required_fallback(item["field"])
    ]


def _override_path(run_dir: Path) -> Path | None:
    schema_path = latest_live_schema(run_dir)
    return schema_path.with_name("required-overrides.json") if schema_path is not None else None


def _user_override_map(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = _override_path(run_dir)
    if path is None or not path.is_file():
        return {}
    try:
        overrides = load_required_overrides(path)
    except Exception:
        return {}
    return {
        str(item.get("field_id") or "").strip(): item
        for item in overrides
        if str(item.get("source_type") or "").strip().casefold() == "user"
        and str(item.get("field_id") or "").strip()
    }


def _protected_missing_user(run_dir: Path) -> list[dict[str, Any]]:
    blocked = _protected_required(run_dir)
    users = _user_override_map(run_dir)
    return [item for item in blocked if item["field_id"] not in users]


def _intent_tokens(intent: str) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", _clean_intent(intent)):
        token = match.group(0)
        candidates = [token]
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 2:
            candidates.extend(token[index : index + 2] for index in range(len(token) - 1))
        for candidate in candidates:
            normalized = normalize_key(candidate)
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            output.append(normalized)
    return output


def _observation_index(run_dir: Path) -> dict[str, str]:
    manifest_path = latest_resolver_manifest(run_dir, "03-hot-resolver")
    if manifest_path is None or not manifest_path.is_file():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        outputs = manifest.get("outputs") or {}
        observation_path = Path(str(outputs.get("image_observations") or ""))
        raw = json.loads(observation_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, list):
        return {}

    output: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        origin = str(item.get("origin") or "").strip()
        if not origin:
            continue
        parts = [str(item.get("visible_text") or ""), str(item.get("notes") or "")]
        for fact in item.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            parts.extend(
                str(fact.get(key) or "")
                for key in ("name", "scope", "value", "qualifier", "evidence_text")
            )
        output[os.path.normcase(os.path.abspath(origin))] = " ".join(parts)
    return output


def rank_listing_images(paths: list[Path] | tuple[Path, ...], run_dir: Path, intent: str) -> list[Path]:
    """Prefer product images whose existing visual observations match the sold offer.

    No new vision call or product semantic classifier exists here. This is only a
    stable lexical ranking over image facts that the Resolver already extracted.
    If the observations do not distinguish the selected offer, original supplier
    order is preserved exactly.
    """

    ordered = [Path(path) for path in paths]
    tokens = _intent_tokens(intent)
    if not ordered or not tokens:
        return ordered
    index = _observation_index(run_dir)
    if not index:
        return ordered

    scored: list[tuple[int, int, Path]] = []
    any_score = False
    for position, path in enumerate(ordered):
        text = normalize_key(index.get(os.path.normcase(os.path.abspath(str(path))), ""))
        score = sum(1 for token in tokens if token and token in text)
        any_score = any_score or score > 0
        scored.append((score, position, path))
    if not any_score:
        return ordered
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored]


@dataclass(slots=True)
class _BatchRequiredPanel:
    host: QFrame
    editors: dict[str, QWidget]


class ListingOfferSupport(QObject):
    """One optional seller offer intent shared by Single, Batch, copy and photos."""

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.batch = window.batch_workspace
        self.controller = self.batch.controller
        self._batch_required_panels: dict[str, _BatchRequiredPanel] = {}
        self._install_single_intent_input()
        self._install_batch_intent_inputs()
        self._install_process_handoff()
        self._install_required_confirmation()
        self._install_photo_ranking()

    # ------------------------------------------------------------------ Single
    def _install_single_intent_input(self) -> None:
        if hasattr(self.window, "listing_intent_input"):
            return
        card = self.window.url_input.parentWidget()
        layout = card.layout() if card is not None else None
        if not isinstance(layout, QVBoxLayout):
            return

        row = QHBoxLayout()
        row.setSpacing(9)
        label = QLabel("销售规格 / 套装")
        label.setObjectName("sectionEyebrow")
        label.setMinimumWidth(96)
        editor = QLineEdit()
        editor.setObjectName("listingIntentInput")
        editor.setPlaceholderText("可选：黑色净化器 + 2瓶香薰精油")
        editor.setToolTip(
            "只描述本次实际上架的颜色 / 规格 / 数量 / 套装。"
            "它用于消除供应商多 SKU 歧义，不是 Makro 的 12 位 Seller SKU。"
        )
        row.addWidget(label, 0, Qt.AlignVCenter)
        row.addWidget(editor, 1)
        # Base input layout: heading=0, URL=1, stage buttons were inserted at 2.
        layout.insertLayout(2, row)
        self.window.listing_intent_input = editor

    # ------------------------------------------------------------------- Batch
    def _decorate_batch_row(self, row: Any) -> None:
        if hasattr(row, "offer_input"):
            return
        layout = row.layout()
        remove = getattr(row, "remove_button", None)
        if not isinstance(layout, QHBoxLayout) or remove is None:
            return
        editor = QLineEdit()
        editor.setObjectName("batchUrlLineEdit")
        editor.setFixedHeight(28)
        editor.setMinimumWidth(220)
        editor.setMaximumWidth(360)
        editor.setPlaceholderText("销售规格 / 套装（可选）")
        editor.setToolTip("例如：黑色净化器 + 2瓶香薰精油；每个链接独立。")
        editor.setEnabled(bool(row.is_enabled()) and not bool(row.editor.locked))
        index = layout.indexOf(remove)
        layout.insertWidget(index if index >= 0 else layout.count(), editor, 0, Qt.AlignVCenter)
        row.offer_input = editor
        row.toggle.toggled.connect(
            lambda checked, current=row: current.offer_input.setEnabled(
                bool(checked) and not bool(current.editor.locked)
            )
        )

    def _install_batch_intent_inputs(self) -> None:
        editor = getattr(self.batch, "_batch_url_editor", None)
        if editor is None:
            return
        for row in list(editor.rows):
            self._decorate_batch_row(row)

        original_add_row = editor.add_row

        def add_row(_editor: Any, *args: Any, **kwargs: Any):
            row = original_add_row(*args, **kwargs)
            self._decorate_batch_row(row)
            return row

        editor.add_row = MethodType(add_row, editor)
        original_set_locked = editor.set_locked

        def set_locked(_editor: Any, locked: bool) -> None:
            original_set_locked(bool(locked))
            for row in list(_editor.rows):
                offer = getattr(row, "offer_input", None)
                if isinstance(offer, QLineEdit):
                    offer.setReadOnly(bool(locked))
                    offer.setEnabled(bool(row.is_enabled()))

        editor.set_locked = MethodType(set_locked, editor)

    def _batch_intent_by_url(self) -> dict[str, str]:
        editor = getattr(self.batch, "_batch_url_editor", None)
        if editor is None:
            return {}
        output: dict[str, str] = {}
        for row in editor.rows:
            url = row.url()
            if not row.is_enabled() or not url:
                continue
            offer = getattr(row, "offer_input", None)
            intent = _clean_intent(offer.text() if isinstance(offer, QLineEdit) else "")
            output[url.casefold()] = intent
        return output

    def _job_intent(self, job: Any) -> str:
        mapping = getattr(self.controller, "_listing_offer_intent_by_url", {})
        return _clean_intent(mapping.get(str(job.product_url).casefold(), "")) if isinstance(mapping, dict) else ""

    # --------------------------------------------------------------- Process env
    def _install_process_handoff(self) -> None:
        original_single_start = self.window._start_mode

        def start_mode(_window: Any, mode: str) -> None:
            intent = _clean_intent(_window.listing_intent_input.text())
            _with_process_intent(intent, lambda: original_single_start(mode))
            run_dir = getattr(_window.runner, "run_dir", None)
            if isinstance(run_dir, Path) and run_dir.exists():
                _write_intent_sidecar(run_dir, intent)

        self.window._start_mode = MethodType(start_mode, self.window)

        original_start_prepare = self.controller.start_prepare

        def start_prepare(_controller: Any, urls: list[str], config: Any, **kwargs: Any):
            mapping = self._batch_intent_by_url()
            _controller._listing_offer_intent_by_url = dict(mapping)
            batch = original_start_prepare(urls, config, **kwargs)
            for job in batch.jobs:
                _write_intent_sidecar(Path(job.run_dir).parent, mapping.get(job.product_url.casefold(), ""))
            return batch

        self.controller.start_prepare = MethodType(start_prepare, self.controller)

        original_spawn = self.controller._spawn

        def spawn(_controller: Any, job_id: str, stage: str, args: list[str]) -> None:
            try:
                job = _controller._job(job_id)
                intent = self._job_intent(job)
            except Exception:
                intent = ""
            _with_process_intent(intent, lambda: original_spawn(job_id, stage, args))
            if stage == "prepare" and intent:
                _controller.log.emit(f"[{job_id}] listing_intent={intent[:180]}")

        self.controller._spawn = MethodType(spawn, self.controller)

        self.controller.jobs_changed.connect(self._annotate_batch_cards)

    def _annotate_batch_cards(self, jobs: list[Any]) -> None:
        for job in jobs:
            card = getattr(self.batch, "_job_cards", {}).get(str(job.job_id))
            if card is None:
                continue
            intent = self._job_intent(job)
            label = getattr(card, "url_label", None)
            if isinstance(label, QLabel):
                label.setText(
                    str(job.product_url)
                    + (f"\n销售规格 · {intent}" if intent else "\n销售规格 · 自动 / 未指定")
                )
            self._sync_batch_required_panel(job)

    # --------------------------------------------------------- Required question
    def _install_required_confirmation(self) -> None:
        support = getattr(self.window, "_required_input_support", None)
        if support is None:
            return

        original_merge = support._merged_overrides
        original_sync = support._sync_button

        def merged(_support: Any) -> list[dict[str, Any]]:
            overrides: list[dict[str, Any]] = []
            missing_labels: list[str] = []
            for identifier, editor in _support.inputs.items():
                value = editor.text().strip()
                field = _support.fields.get(identifier)
                if field is None:
                    continue
                if value:
                    overrides.append(
                        {
                            **required_override_binding(field),
                            "values": [value],
                            "source_type": "user",
                        }
                    )
                elif allow_required_fallback(field):
                    overrides.append(required_fallback_override(field))
                else:
                    missing_labels.append(_support.labels.get(identifier, identifier))
            if missing_labels:
                raise RuntimeError(
                    "这些关键必填字段不能使用 N/A / 1 / 随机选项，请先确认："
                    + "、".join(missing_labels)
                )
            return overrides

        support._merged_overrides = MethodType(merged, support)

        def sync(_support: Any) -> None:
            original_sync()
            if _support.window.real_scope_combo.currentData() != FULL_STEP3:
                return
            missing = [
                _support.labels.get(identifier, identifier)
                for identifier, editor in _support.inputs.items()
                if not editor.text().strip()
                and identifier in _support.fields
                and not allow_required_fallback(_support.fields[identifier])
            ]
            if missing:
                _support.window.real_start_button.setEnabled(False)
                _support.window.real_start_button.setToolTip(
                    "请先确认关键必填字段：" + "、".join(missing)
                )

        support._sync_button = MethodType(sync, support)

        def refresh(result: Any) -> None:
            protected: list[str] = []
            ordinary = 0
            for identifier, editor in support.inputs.items():
                field = support.fields.get(identifier)
                if field is None:
                    continue
                if allow_required_fallback(field):
                    ordinary += 1
                    continue
                label = support.labels.get(identifier, identifier)
                protected.append(label)
                editor.setPlaceholderText("必填 · 请确认（不会自动填 N/A / 1）")
                editor.setToolTip(
                    (editor.toolTip() or "")
                    + "\n\n该字段会影响标题、销售清单、标识或合规信息；禁止通用占位兜底。"
                )
            if protected:
                support.window.fields_hint.setText(
                    f"READY={result.ready} · {len(protected)} 个关键必填需确认"
                    + (f" · {ordinary} 个普通必填可固定兜底" if ordinary else "")
                )
                support.window.real_policy_hint.setText(
                    "关键字段不会再写 N/A / 1 / 随机 option。请在字段表确认："
                    + "、".join(protected)
                    + ("；其余普通 required 仍使用现有机械兜底。" if ordinary else "。")
                )
            support._sync_button()

        self.window.runner.result_updated.connect(refresh)

        original_finished = self.controller._finished

        def finished(_controller: Any, process: Any, exit_code: int) -> None:
            ownership = _controller._processes.get(process, ("", ""))
            original_finished(process, exit_code)
            job_id, stage = ownership
            if stage != "prepare" or int(exit_code) != 0 or not job_id:
                return
            try:
                job = _controller._job(job_id)
                missing = _protected_missing_user(Path(job.run_dir))
            except Exception:
                return
            if not missing:
                return
            labels = [str(item.get("label") or item["field_id"]) for item in missing]
            job.status = "REVIEW"
            job.stage_detail = "需确认关键字段"
            job.error = "关键必填待确认：" + "、".join(labels)
            job.touch()
            _controller._persist_emit()

        self.controller._finished = MethodType(finished, self.controller)

        original_execution_args = self.controller._execution_args

        def execution_args(_controller: Any, job: Any) -> list[str]:
            run_dir = Path(job.run_dir)
            protected = _protected_required(run_dir)
            users = _user_override_map(run_dir)
            missing = [item for item in protected if item["field_id"] not in users]
            if missing:
                raise RuntimeError(
                    f"{job.job_id} 仍有关键必填未确认："
                    + "、".join(str(item.get("label") or item["field_id"]) for item in missing)
                )

            protected_users = {
                item["field_id"]: users[item["field_id"]]
                for item in protected
                if item["field_id"] in users
            }
            args = original_execution_args(job)

            # The canonical Batch path just regenerated ordinary deterministic
            # fallbacks. Replace any protected placeholder with the explicit user
            # value before the executor starts; all existing live option/unit hard
            # guards still run inside apply_required_overrides.
            override_path = _override_path(run_dir)
            if override_path is not None and protected_users:
                generated: list[dict[str, Any]] = []
                if override_path.is_file():
                    try:
                        generated = load_required_overrides(override_path)
                    except Exception:
                        generated = []
                protected_ids = set(protected_users)
                merged_overrides = [
                    item for item in generated
                    if str(item.get("field_id") or "") not in protected_ids
                ]
                merged_overrides.extend(protected_users.values())
                override_path.write_text(
                    json.dumps({"overrides": merged_overrides}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            return self._rank_batch_upload_args(job, args)

        self.controller._execution_args = MethodType(execution_args, self.controller)

    def _sync_batch_required_panel(self, job: Any) -> None:
        job_id = str(job.job_id)
        card = getattr(self.batch, "_job_cards", {}).get(job_id)
        if card is None:
            return
        try:
            missing = _protected_missing_user(Path(job.run_dir))
        except Exception:
            missing = []
        existing = self._batch_required_panels.get(job_id)
        if not missing:
            if existing is not None:
                existing.host.hide()
            return
        if existing is None:
            existing = self._create_batch_required_panel(card, job_id)
            self._batch_required_panels[job_id] = existing
        self._populate_batch_required_panel(existing, missing, job_id)
        existing.host.show()

    def _create_batch_required_panel(self, card: Any, job_id: str) -> _BatchRequiredPanel:
        host = QFrame(card)
        host.setObjectName("batchRequiredConfirm")
        host.setStyleSheet(
            "QFrame#batchRequiredConfirm {"
            "background:rgba(108,75,25,54);border:1px solid rgba(255,210,124,64);"
            "border-radius:9px;}"
        )
        box = QVBoxLayout(host)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(6)
        title = QLabel("需要确认 · 关键 listing 字段")
        title.setObjectName("sectionEyebrow")
        box.addWidget(title)
        button = QPushButton("确认关键字段")
        button.setObjectName("primaryButton")
        button.clicked.connect(lambda _checked=False, jid=job_id: self._confirm_batch_required(jid))
        host._confirm_button = button  # type: ignore[attr-defined]
        root = card.layout()
        details = getattr(card, "details_box", None)
        index = root.indexOf(details) if details is not None else root.count()
        root.insertWidget(index if index >= 0 else root.count(), host)
        return _BatchRequiredPanel(host=host, editors={})

    def _populate_batch_required_panel(
        self,
        panel: _BatchRequiredPanel,
        missing: list[dict[str, Any]],
        job_id: str,
    ) -> None:
        box = panel.host.layout()
        button = getattr(panel.host, "_confirm_button", None)
        # Rebuild only the small question rows. This path is activated for REVIEW
        # jobs, not for high-frequency progress telemetry.
        for editor in panel.editors.values():
            row_host = editor.property("questionRow")
            if isinstance(row_host, QWidget):
                box.removeWidget(row_host)
                row_host.deleteLater()
        panel.editors.clear()

        for item in missing:
            identifier = str(item["field_id"])
            row_host = QWidget(panel.host)
            row = QHBoxLayout(row_host)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            label = QLabel(str(item.get("label") or identifier))
            label.setMinimumWidth(150)
            options = [str(value) for value in item.get("options") or [] if str(value).strip()]
            if options:
                editor: QWidget = QComboBox()
                editor.addItem("请选择…", "")
                for option in options:
                    editor.addItem(option, option)
            else:
                editor = QLineEdit()
                editor.setPlaceholderText("输入本次 listing 的准确值")
            editor.setProperty("questionRow", row_host)
            row.addWidget(label)
            row.addWidget(editor, 1)
            box.insertWidget(max(1, box.count() - 1), row_host)
            panel.editors[identifier] = editor
        if isinstance(button, QPushButton):
            button.setText(f"确认 {len(missing)} 个关键字段")

    @staticmethod
    def _editor_value(editor: QWidget) -> str:
        if isinstance(editor, QLineEdit):
            return editor.text().strip()
        if isinstance(editor, QComboBox):
            return str(editor.currentData() or "").strip()
        return ""

    def _confirm_batch_required(self, job_id: str) -> None:
        try:
            job = self.controller._job(job_id)
            missing = _protected_missing_user(Path(job.run_dir))
        except Exception:
            return
        panel = self._batch_required_panels.get(job_id)
        if panel is None:
            return
        values = {
            identifier: self._editor_value(editor)
            for identifier, editor in panel.editors.items()
        }
        missing_values = [
            str(item.get("label") or item["field_id"])
            for item in missing
            if not values.get(str(item["field_id"]), "")
        ]
        if missing_values:
            panel.host.setToolTip("仍需填写：" + "、".join(missing_values))
            return

        overrides: list[dict[str, Any]] = []
        for item in missing:
            identifier = str(item["field_id"])
            overrides.append(
                {
                    **required_override_binding(item["field"]),
                    "values": [values[identifier]],
                    "source_type": "user",
                }
            )
        path = _override_path(Path(job.run_dir))
        if path is None:
            return
        path.write_text(
            json.dumps({"overrides": overrides}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        job.status = "READY"
        job.stage_detail = "关键字段已确认 · 可真实填写"
        job.error = ""
        job.touch()
        panel.host.hide()
        self.controller._persist_emit()

    # --------------------------------------------------------------- Photo rank
    def _install_photo_ranking(self) -> None:
        original = self.window._current_resolver_product_images

        def current_images(_window: Any):
            paths = list(original())
            result = getattr(_window, "current_result", None)
            intent = _clean_intent(_window.listing_intent_input.text())
            if result is None or not intent:
                return tuple(paths)
            return tuple(rank_listing_images(paths, Path(result.run_dir), intent))

        self.window._current_resolver_product_images = MethodType(current_images, self.window)

    def _rank_batch_upload_args(self, job: Any, args: list[str]) -> list[str]:
        if not bool(getattr(self.controller, "_execution_images", False)):
            return args
        intent = self._job_intent(job)
        if not intent:
            return args
        manifest_path = latest_resolver_manifest(Path(job.run_dir), "03-hot-resolver")
        if manifest_path is None or not manifest_path.is_file():
            return args
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            outputs = manifest.get("outputs") or {}
            images = [
                Path(str(value))
                for value in outputs.get("primary_source_product_images") or []
                if str(value).strip() and Path(str(value)).is_file()
            ]
        except Exception:
            return args
        ranked = rank_listing_images(images, Path(job.run_dir), intent)[:5]
        if not ranked:
            return args

        stripped: list[str] = []
        index = 0
        while index < len(args):
            if args[index] == "--upload-image" and index + 1 < len(args):
                index += 2
                continue
            stripped.append(args[index])
            index += 1
        for image in ranked:
            stripped.extend(["--upload-image", str(image)])
        return stripped


def install_listing_offer_support(window: Any) -> ListingOfferSupport:
    existing = getattr(window, "_listing_offer_support", None)
    if isinstance(existing, ListingOfferSupport):
        return existing
    support = ListingOfferSupport(window)
    window._listing_offer_support = support
    return support


__all__ = ["ListingOfferSupport", "install_listing_offer_support", "rank_listing_images"]
