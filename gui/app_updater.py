"""Velopack-backed Stable update presentation for Listing Studio.

Listing Studio owns only product policy and presentation: when to offer an update,
whether listing work is idle, closing its dedicated Makro browser, and showing the
branded progress surface. Velopack owns release discovery, package download,
process handoff, replacement, rollback and restart.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QWidget

from app.update_browser_gate import DEFAULT_CDP_PORT, close_managed_browser
from app.velopack_runtime import (
    create_update_manager,
    installed_application_version,
    is_velopack_managed,
    update_summary,
)
from gui.update_panel import UpdateMessageDialog, UpdateOfferDialog, UpdateProgressDialog
from gui.update_runtime import shutdown_owned_qprocesses

_CHECK_DELAY_MS = 1800
_AUTO_CHECK_INTERVAL_MS = 60 * 60 * 1000


def _format_size(size: int) -> str:
    value = max(0, int(size or 0))
    if value <= 0:
        return ""
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


class ApplicationUpdater(QObject):
    """Thin branded Qt presentation over Velopack's standard lifecycle."""

    _check_finished = Signal(object)
    _download_progress = Signal(int)
    _update_finished = Signal(object)

    def __init__(self, window: QMainWindow, *, access_controller: Any | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.access_controller = access_controller
        self.current_version = installed_application_version()
        self._manual_check = False
        self._checking = False
        self._updating = False
        self._last_prompted_version: str | None = None
        self._progress: UpdateProgressDialog | None = None
        self._check_button: QPushButton | None = None
        self._version_label: QLabel | None = None
        self._threads: set[threading.Thread] = set()

        self._check_finished.connect(self._on_check_finished)
        self._download_progress.connect(self._on_download_progress)
        self._update_finished.connect(self._on_update_finished)

        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(_AUTO_CHECK_INTERVAL_MS)
        self._auto_timer.timeout.connect(self.check_for_updates)
        self._install_header_controls()

    @staticmethod
    def enabled() -> bool:
        disabled = os.getenv("ECOMMERCE_AGENT_DISABLE_UPDATE_CHECK", "").strip().lower()
        return (
            bool(getattr(sys, "frozen", False))
            and is_velopack_managed()
            and disabled not in {"1", "true", "yes"}
        )

    def _run_thread(self, target: Callable[[], None], name: str) -> None:
        def _wrapped() -> None:
            try:
                target()
            finally:
                self._threads.discard(threading.current_thread())

        thread = threading.Thread(target=_wrapped, name=name, daemon=True)
        self._threads.add(thread)
        thread.start()

    def _bring_to_front(self, dialog: QWidget) -> None:
        try:
            if self.window.isMinimized():
                self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
            dialog.raise_()
            dialog.activateWindow()
        except RuntimeError:
            pass

    def _show_message(
        self,
        _icon: QMessageBox.Icon,
        text: str,
        *,
        informative: str = "",
        details: str = "",
    ) -> int:
        dialog = UpdateMessageDialog(
            self.window,
            title=text,
            message=informative,
            details=details,
        )
        QTimer.singleShot(0, lambda: self._bring_to_front(dialog))
        return dialog.exec()

    def _install_header_controls(self) -> None:
        root = self.window.centralWidget()
        outer = root.layout() if isinstance(root, QWidget) else None
        header = outer.itemAt(0).layout() if outer is not None and outer.count() else None
        if not isinstance(header, QBoxLayout):
            return

        version_label = QLabel(f"v{self.current_version}  ·  STABLE", self.window)
        version_label.setObjectName("appVersionBadge")
        version_label.setFixedHeight(32)
        version_label.setToolTip(f"当前版本 v{self.current_version} · Stable channel")
        version_label.setStyleSheet(
            "QLabel#appVersionBadge { padding: 0 10px; color: rgba(255,255,255,194);"
            " background: rgba(20,24,34,70); border: 1px solid rgba(255,255,255,28);"
            " border-radius: 10px; font-size: 11px; font-weight: 650; }"
        )

        check_button = QPushButton("检查更新", self.window)
        check_button.setObjectName("checkUpdateButton")
        check_button.setFixedHeight(32)
        check_button.setToolTip("检查 Stable 更新通道")
        check_button.setStyleSheet(
            "QPushButton#checkUpdateButton { min-height: 30px; max-height: 30px; padding: 0 12px;"
            " color: rgba(255,255,255,220); background: rgba(20,24,34,62);"
            " border: 1px solid rgba(255,255,255,26); border-radius: 10px;"
            " font-size: 11px; font-weight: 650; }"
            "QPushButton#checkUpdateButton:hover { background: rgba(255,255,255,30);"
            " border-color: rgba(255,255,255,44); }"
        )
        check_button.clicked.connect(self.manual_check_for_updates)

        phase_badge = getattr(self.window, "phase_badge", None)
        index = header.indexOf(phase_badge) if isinstance(phase_badge, QWidget) else -1
        if index < 0:
            index = header.count()
        header.insertWidget(index, version_label, 0, Qt.AlignmentFlag.AlignBottom)
        header.insertWidget(index + 1, check_button, 0, Qt.AlignmentFlag.AlignBottom)
        self._version_label = version_label
        self._check_button = check_button
        self.window.app_version_label = version_label  # type: ignore[attr-defined]
        self.window.check_update_button = check_button  # type: ignore[attr-defined]

    def _set_manual_check_busy(self, busy: bool) -> None:
        if self._check_button is None:
            return
        try:
            self._check_button.setEnabled(not busy and not self._updating)
            self._check_button.setText("检查中…" if busy else "检查更新")
        except RuntimeError:
            pass

    def schedule_startup_check(self) -> None:
        if not self.enabled():
            return
        QTimer.singleShot(_CHECK_DELAY_MS, self.check_for_updates)
        if not self._auto_timer.isActive():
            self._auto_timer.start()

    def manual_check_for_updates(self) -> None:
        if not self.enabled():
            message = "当前不是 Velopack 管理的正式安装版。"
            if bool(getattr(sys, "frozen", False)):
                message += "\n\n旧安装版或便携版只需从官网下载并安装一次新的 Listing Studio；之后更新全部由 Velopack 接管。"
            else:
                message += "\n\n源码开发模式不连接正式更新通道。"
            self._show_message(
                QMessageBox.Icon.Information,
                f"当前版本 v{self.current_version}",
                informative=message,
            )
            return
        self.check_for_updates(manual=True)

    def check_for_updates(self, *, manual: bool = False) -> None:
        if not self.enabled() or self._checking or self._updating:
            return
        self._checking = True
        self._manual_check = bool(manual)
        if manual:
            self._set_manual_check_busy(True)

        def _worker() -> None:
            try:
                manager = create_update_manager()
                info = manager.check_for_updates()
                payload = {"ok": True, "summary": update_summary(info) if info else None}
            except Exception as exc:
                payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._check_finished.emit(payload)

        self._run_thread(_worker, "listing-studio-velopack-check")

    def _on_check_finished(self, payload_obj: object) -> None:
        self._checking = False
        manual = self._manual_check
        self._manual_check = False
        self._set_manual_check_busy(False)
        payload = dict(payload_obj) if isinstance(payload_obj, dict) else {}
        if not payload.get("ok"):
            if manual:
                self._show_message(
                    QMessageBox.Icon.Warning,
                    "暂时无法检查更新。",
                    informative=str(payload.get("error") or "Velopack update check failed"),
                )
            return

        summary = payload.get("summary")
        if not isinstance(summary, dict):
            if manual:
                self._show_message(
                    QMessageBox.Icon.Information,
                    f"当前已是最新版本 v{self.current_version}。",
                    informative="Stable 通道没有比当前版本更新的发布。",
                )
            return

        latest = str(summary.get("version") or "").strip().lstrip("v")
        if not latest:
            return
        if not manual and latest == self._last_prompted_version:
            return
        self._last_prompted_version = latest
        self._prompt_for_update(summary)

    def _browser_manager(self) -> Any | None:
        return getattr(self.window, "_managed_makro_browser", None)

    def _prompt_for_update(self, summary: dict[str, Any]) -> None:
        manager = self._browser_manager()
        if manager is not None and hasattr(manager, "is_busy"):
            try:
                if bool(manager.is_busy()):
                    self._last_prompted_version = None
                    self._show_message(
                        QMessageBox.Icon.Information,
                        "当前有上架任务正在运行，暂不开始更新。",
                        informative="请等待 Single / Real Execution / Batch 任务结束后再次检查更新。",
                    )
                    return
            except Exception:
                return

        latest = str(summary.get("version") or "").strip().lstrip("v")
        notes = str(summary.get("notes") or "").strip()
        size = _format_size(int(summary.get("size") or 0))
        dialog = UpdateOfferDialog(
            self.window,
            current_version=self.current_version,
            target_version=latest,
            package_size=size,
            notes=notes,
        )
        QTimer.singleShot(0, lambda: self._bring_to_front(dialog))
        if dialog.exec() == UpdateOfferDialog.DialogCode.Accepted:
            self._begin_update(latest)

    def _open_progress(self, target_version: str) -> None:
        progress = UpdateProgressDialog(self.window, target_version=target_version)
        progress.show()
        self._progress = progress
        self._bring_to_front(progress)

    def _set_progress_text(self, text: str) -> None:
        if self._progress is None:
            return
        try:
            self._progress.set_stage(text)
            self._bring_to_front(self._progress)
        except RuntimeError:
            pass

    def _close_progress(self) -> None:
        progress = self._progress
        self._progress = None
        if progress is not None:
            try:
                progress.done(UpdateProgressDialog.DialogCode.Accepted)
                progress.deleteLater()
            except RuntimeError:
                pass

    def _resume_browser_manager(self) -> None:
        manager = self._browser_manager()
        if manager is not None and hasattr(manager, "resume_after_update_failure"):
            try:
                manager.resume_after_update_failure()
            except Exception:
                pass

    def _fail_update(self, error: str) -> None:
        self._updating = False
        self._resume_browser_manager()
        self._last_prompted_version = None
        self._close_progress()
        self._show_message(
            QMessageBox.Icon.Critical,
            "更新未完成，Listing Studio 已保持当前版本运行。",
            informative=error,
        )

    def _begin_update(self, target_version: str) -> None:
        if self._updating:
            return
        browser_manager = self._browser_manager()
        if browser_manager is not None and hasattr(browser_manager, "begin_update_quiesce"):
            try:
                ready, reason = browser_manager.begin_update_quiesce()
            except Exception as exc:
                ready, reason = False, str(exc)
            if not ready:
                self._last_prompted_version = None
                self._show_message(
                    QMessageBox.Icon.Information,
                    "当前状态不适合进入更新。",
                    informative=str(reason or "请等待当前任务结束后重试。"),
                )
                return

        self._updating = True
        self._set_manual_check_busy(False)
        self._open_progress(target_version)

        def _worker() -> None:
            try:
                manager = create_update_manager()
                info = manager.check_for_updates()
                if info is None:
                    raise RuntimeError("更新在下载前已从 Stable 通道撤回，请稍后重新检查。")
                summary = update_summary(info)
                actual = str(summary.get("version") or "").strip().lstrip("v")
                if actual != target_version:
                    raise RuntimeError(
                        f"Stable 更新目标在确认后发生变化：expected={target_version} actual={actual}"
                    )

                def _progress(value: Any) -> None:
                    try:
                        percent = max(0, min(100, int(value)))
                    except (TypeError, ValueError):
                        percent = 0
                    self._download_progress.emit(percent)

                manager.download_updates(info, _progress)
                if manager.get_update_pending_restart() is None:
                    raise RuntimeError("Velopack 下载完成但没有生成可应用的更新包。")

                if browser_manager is not None and hasattr(browser_manager, "wait_for_update_quiesce"):
                    ready, reason = browser_manager.wait_for_update_quiesce(20.0)
                    if not ready:
                        raise RuntimeError(str(reason or "Makro Browser 更新冻结没有完成。"))

                closed = close_managed_browser(port=DEFAULT_CDP_PORT)
                if not closed.ok:
                    raise RuntimeError(closed.detail or "无法安全关闭 Makro Browser。")

                self._update_finished.emit({"ok": True, "version": target_version})
            except Exception as exc:
                self._update_finished.emit(
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                )

        self._run_thread(_worker, "listing-studio-velopack-update")

    def _on_download_progress(self, value: int) -> None:
        if self._progress is None:
            return
        try:
            percent = max(0, min(100, int(value)))
            self._progress.set_progress(percent)
            self._progress.set_stage(f"正在下载并校验更新包… {percent}%")
        except RuntimeError:
            pass

    def _apply_downloaded_update(self) -> None:
        if not self._updating:
            return
        try:
            manager = create_update_manager()
            pending = manager.get_update_pending_restart()
            if pending is None:
                raise RuntimeError("Velopack 未找到已经下载完成的待应用更新。")
            self._set_progress_text("正在关闭后台组件并准备切换到新版本…")
            shutdown_owned_qprocesses(self.window)
            manager.apply_updates_and_restart(pending)
            raise RuntimeError("Velopack 更新器返回了控制权，程序未按预期退出并重启。")
        except Exception as exc:
            self._fail_update(f"{type(exc).__name__}: {exc}")

    def _on_update_finished(self, payload_obj: object) -> None:
        payload = dict(payload_obj) if isinstance(payload_obj, dict) else {}
        if not payload.get("ok"):
            self._fail_update(str(payload.get("error") or "Velopack update failed"))
            return

        self._set_progress_text("下载与校验完成。正在关闭当前版本并自动启动新版本…")
        if self._progress is not None:
            try:
                self._progress.set_progress(100)
            except RuntimeError:
                pass
        # Give the branded surface one final paint, then use Velopack's explicit
        # process-exit/apply/restart primitive. All app-owned QProcess children are
        # stopped immediately before the handoff so no worker can keep files or
        # runtime state alive across the version switch.
        QTimer.singleShot(120, self._apply_downloaded_update)


def install_application_updater(
    window: QMainWindow,
    *,
    access_controller: Any | None = None,
) -> ApplicationUpdater:
    existing = getattr(window, "_application_updater", None)
    if isinstance(existing, ApplicationUpdater):
        return existing
    updater = ApplicationUpdater(window, access_controller=access_controller)
    window._application_updater = updater  # type: ignore[attr-defined]
    updater.schedule_startup_check()
    return updater


__all__ = ["ApplicationUpdater", "install_application_updater"]
