from __future__ import annotations

import json
from pathlib import Path
from types import MethodType
from typing import Any
from urllib.parse import urlparse

from PySide6.QtWidgets import QComboBox, QLineEdit, QMessageBox

from .batch_lifecycle import install_batch_lifecycle
from .readonly_runner import RunnerConfig


_INTENT_SIDECAR = "listing-intent.json"


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()[:600]


def _sidecar_intent(run_dir: Path) -> str:
    path = run_dir.resolve() / _INTENT_SIDECAR
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return _clean(payload.get("listing_intent")) if isinstance(payload, dict) else ""


def _write_sidecar(run_dir: Path, intent: str) -> None:
    path = run_dir.resolve() / _INTENT_SIDECAR
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "listing_intent": _clean(intent),
                "kind": "listing_offer_scope",
                "source": "user",
                "note": "Per-listing sold variant/bundle scope; not the Makro seller SKU identifier.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _validate_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"不是完整 http(s) 商品链接：{url}")
    return url


class ListingOfferHardening:
    """Close ownership gaps around the optional per-listing offer intent.

    This layer does not add a second Resolver or executor. It only freezes which
    seller-selected variant/bundle belongs to which existing Single run or Batch
    Job, and fails closed when the UI scope no longer matches prepared evidence.
    """

    def __init__(self, window: Any) -> None:
        self.window = window
        self.batch = window.batch_workspace
        self.controller = self.batch.controller
        self.support = getattr(window, "_listing_offer_support", None)
        if self.support is None:
            raise RuntimeError("listing offer hardening requires ListingOfferSupport")

        self._prepared_single_run: Path | None = None
        self._prepared_single_intent = ""
        self._dirty_hint_backup = ""

        self._install_batch_job_owned_intents()
        self._install_single_intent_freeze()

    # ----------------------------------------------------------------- Batch
    def _batch_entries(self) -> list[tuple[str, str]]:
        editor = getattr(self.batch, "_batch_url_editor", None)
        if editor is None:
            raise RuntimeError("Batch listing offer requires the multi-link editor")
        entries: list[tuple[str, str]] = []
        for row in list(editor.rows):
            if not row.is_enabled():
                continue
            raw_url = row.url()
            if not raw_url:
                continue
            offer = getattr(row, "offer_input", None)
            intent = _clean(offer.text() if isinstance(offer, QLineEdit) else "")
            entries.append((_validate_url(raw_url), intent))
        if not entries:
            raise ValueError("请至少输入一个供应商商品链接。")
        return entries

    def _install_batch_job_owned_intents(self) -> None:
        original_job_intent = self.support._job_intent

        def job_intent(_support: Any, job: Any) -> str:
            owned = getattr(self.controller, "_listing_offer_intent_by_job_id", {})
            if isinstance(owned, dict):
                value = owned.get(str(job.job_id))
                if value is not None:
                    return _clean(value)
            return _clean(original_job_intent(job))

        self.support._job_intent = MethodType(job_intent, self.support)

        original_controller_start = self.controller.start_prepare

        def controller_start(
            _controller: Any,
            urls: list[str],
            config: RunnerConfig,
            **kwargs: Any,
        ):
            pending = list(getattr(_controller, "_listing_offer_pending_intents", []))
            batch = original_controller_start(urls, config, **kwargs)
            if len(pending) == len(batch.jobs):
                owned = {
                    str(job.job_id): _clean(pending[index])
                    for index, job in enumerate(batch.jobs)
                }
                _controller._listing_offer_intent_by_job_id = owned
                for job in batch.jobs:
                    _write_sidecar(Path(job.run_dir).parent, owned.get(str(job.job_id), ""))
            return batch

        self.controller.start_prepare = MethodType(controller_start, self.controller)

        def workspace_start(_workspace: Any) -> None:
            if _workspace.busy_guard():
                QMessageBox.warning(
                    _workspace,
                    "无法开始 Batch",
                    "Single workflow / real execution 仍在运行。",
                )
                return
            try:
                entries = self._batch_entries()
                urls = [item[0] for item in entries]
                intents = [item[1] for item in entries]
                config = RunnerConfig(
                    product_url=urls[0],
                    makro_cdp_port=int(_workspace.makro_port.value()),
                    source_cdp_port=int(_workspace.source_port.value()),
                    source_use_current_page=False,
                )
                _workspace.save_check.setChecked(False)
                _workspace.images_check.setChecked(False)
                self.controller._listing_offer_pending_intents = list(intents)
                self.controller.start_prepare(
                    urls,
                    config,
                    prepare_concurrency=int(_workspace.worker_count.value()),
                )
                _workspace.open_batch_button.setEnabled(True)
            except Exception as exc:
                QMessageBox.critical(_workspace, "批量准备无法启动", str(exc))
            finally:
                self.controller._listing_offer_pending_intents = []

        # Deliberately bypass normalize_batch_urls() here. Rows are validated one
        # by one and duplicates are meaningful when their offer intents differ:
        # same supplier page + Black, same supplier page + White are two listings.
        self.batch._start_prepare = MethodType(workspace_start, self.batch)

    # --------------------------------------------------------------- Single
    def _install_single_intent_freeze(self) -> None:
        editor = getattr(self.window, "listing_intent_input", None)
        if not isinstance(editor, QLineEdit):
            return

        original_start = self.window._start_mode

        def start_mode(_window: Any, mode: str) -> None:
            launch_intent = _clean(editor.text())
            original_start(mode)
            run_dir = getattr(_window.runner, "run_dir", None)
            if mode in {"step3", "full"} and isinstance(run_dir, Path):
                _write_sidecar(run_dir, launch_intent)

        self.window._start_mode = MethodType(start_mode, self.window)
        editor.textChanged.connect(lambda _text: self._sync_single_gate())
        self.window.real_scope_combo.currentIndexChanged.connect(
            lambda _index: self._sync_single_gate()
        )
        self.window.runner.result_updated.connect(self._remember_single_result)

    def _remember_single_result(self, result: Any) -> None:
        if str(getattr(result, "workflow_mode", "")) not in {"step3", "full"}:
            return
        run_dir = Path(getattr(result, "run_dir"))
        self._prepared_single_run = run_dir.resolve()
        self._prepared_single_intent = _sidecar_intent(run_dir)
        self.window._prepared_listing_intent = self._prepared_single_intent

        # Existing RequiredInputSupport connected its original _sync_button before
        # this hardening layer was installed. Add one late observer to each editor
        # so our intent-drift gate always runs after those legacy enable updates.
        required = getattr(self.window, "_required_input_support", None)
        for field_editor in list(getattr(required, "inputs", {}).values()):
            if not isinstance(field_editor, QLineEdit):
                continue
            if bool(field_editor.property("listingIntentGateConnected")):
                continue
            field_editor.setProperty("listingIntentGateConnected", True)
            field_editor.textChanged.connect(lambda _text: self._sync_single_gate())
        self._sync_single_gate()

    def _sync_single_gate(self) -> None:
        editor = getattr(self.window, "listing_intent_input", None)
        result = getattr(self.window, "current_result", None)
        if not isinstance(editor, QLineEdit) or result is None:
            return
        if str(getattr(result, "workflow_mode", "")) not in {"step3", "full"}:
            return
        run_dir = Path(getattr(result, "run_dir")).resolve()
        if self._prepared_single_run != run_dir:
            prepared = _sidecar_intent(run_dir)
            self._prepared_single_run = run_dir
            self._prepared_single_intent = prepared

        current = _clean(editor.text())
        dirty = current != self._prepared_single_intent
        if dirty:
            self.window.real_start_button.setEnabled(False)
            self.window.real_start_button.setToolTip(
                "销售规格 / 套装已在准备后修改。请重新运行 Step 3 或完整准备，不能把旧 Resolver 结果写到新套装。"
            )
            hint = getattr(self.window, "real_policy_hint", None)
            if hint is not None:
                if not self._dirty_hint_backup:
                    self._dirty_hint_backup = hint.text()
                hint.setText(
                    "销售规格已变化 · 当前 Fill Plan 已失效。重新准备后才允许真实填写。"
                )
            return

        hint = getattr(self.window, "real_policy_hint", None)
        if hint is not None and self._dirty_hint_backup:
            hint.setText(self._dirty_hint_backup)
            self._dirty_hint_backup = ""

        # Do not blindly enable the real button. Hand control back to the existing
        # required-field gate, which still owns READY/required policy decisions.
        required = getattr(self.window, "_required_input_support", None)
        sync = getattr(required, "_sync_button", None)
        if callable(sync):
            sync()


def install_listing_offer_hardening(window: Any) -> ListingOfferHardening:
    existing = getattr(window, "_listing_offer_hardening", None)
    if isinstance(existing, ListingOfferHardening):
        install_batch_lifecycle(window.batch_workspace)
        return existing
    hardening = ListingOfferHardening(window)
    window._listing_offer_hardening = hardening
    install_batch_lifecycle(window.batch_workspace)
    return hardening


__all__ = ["ListingOfferHardening", "install_listing_offer_hardening"]
