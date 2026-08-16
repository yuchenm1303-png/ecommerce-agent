"""Formal Listing Studio updater presentation over the canonical update state machine.

Transport, release validation, download and handoff stay in ``gui.app_updater``.
This module owns only the user-facing progress surface and moves the expensive
installer SHA-256 verification and managed-browser shutdown off the Qt GUI thread
so the update panel never appears frozen before standalone updater handoff.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

import gui.app_updater as canonical
from app.update_browser_gate import DEFAULT_CDP_PORT, close_managed_browser


def _six_step_label(text: str) -> str:
    value = str(text or "")
    replacements = {
        "步骤 1/4": "步骤 1/6",
        "步骤 2/4": "步骤 2/6",
        "步骤 3/4": "步骤 3/6",
        "步骤 4/4": "步骤 4/6",
    }
    for old, new in replacements.items():
        if value.startswith(old):
            return new + value[len(old) :]
    if value.startswith("正在验证更新权限"):
        return f"步骤 1/6 · {value}"
    if value.startswith("授权完成") or value.startswith("正在下载"):
        return f"步骤 2/6 · {value}"
    if value.startswith("下载完成") or value.startswith("正在校验"):
        return f"步骤 3/6 · {value}"
    if value.startswith("校验通过") or value.startswith("正在启动更新执行器"):
        return f"步骤 4/6 · {value}"
    return value


class _UpdateProgressPanel(QDialog):
    """One stable always-on-top panel for download/verify/handoff phases."""

    canceled = Signal()

    def __init__(self, parent: QMainWindow) -> None:
        super().__init__(parent)
        self.setWindowTitle("Listing Studio 更新")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumSize(620, 190)
        self.resize(620, 190)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 24)
        layout.setSpacing(18)

        self._label = QLabel("正在准备更新…", self)
        self._label.setWordWrap(True)
        self._label.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(self._label)

        self._bar = QProgressBar(self)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setRange(0, 0)
        layout.addWidget(self._bar)

        footer = QHBoxLayout()
        self._hint = QLabel("更新期间请保持网络连接。", self)
        self._hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        footer.addWidget(self._hint, 1)
        self._cancel = QPushButton("取消", self)
        self._cancel.clicked.connect(self._request_cancel)
        footer.addWidget(self._cancel, 0)
        layout.addLayout(footer)
        self._cancellable = True
        self._allow_close = False

    def _request_cancel(self) -> None:
        if not self._cancellable:
            return
        self._cancel.setEnabled(False)
        self._label.setText("正在取消更新请求…")
        self.canceled.emit()

    def close(self) -> bool:  # type: ignore[override]
        self._allow_close = True
        return super().close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._allow_close:
            event.accept()
            return
        if self._cancellable:
            self._request_cancel()
        event.ignore()

    def setLabelText(self, text: str) -> None:
        self._label.setText(_six_step_label(text))

    def setRange(self, minimum: int, maximum: int) -> None:
        self._bar.setRange(int(minimum), int(maximum))

    def setValue(self, value: int) -> None:
        self._bar.setValue(int(value))

    def setCancelButtonText(self, text: str) -> None:
        self._cancellable = bool(str(text or "").strip())
        self._cancel.setVisible(self._cancellable)
        self._cancel.setEnabled(self._cancellable)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, self._cancellable)
        if self.isVisible():
            self.show()


class ApplicationUpdater(canonical.ApplicationUpdater):
    """Canonical updater with a responsive six-stage progress presentation."""

    _checksum_ready = Signal(str, str, object)
    _browser_close_ready = Signal(str, object, object)

    def __init__(self, window: QMainWindow, *, access_controller: Any | None = None) -> None:
        super().__init__(window, access_controller=access_controller)
        self._verify_thread: threading.Thread | None = None
        self._browser_thread: threading.Thread | None = None
        self._checksum_ready.connect(self._finish_verified_install)
        self._browser_close_ready.connect(self._finish_browser_close)

    def _ensure_progress(self, label: str, *, cancellable: bool) -> _UpdateProgressPanel:
        progress = self._progress
        if not isinstance(progress, _UpdateProgressPanel):
            if progress is not None:
                progress.close()
                progress.deleteLater()
            progress = _UpdateProgressPanel(self.window)
            self._progress = progress  # type: ignore[assignment]
        progress.setLabelText(label)
        progress.setRange(0, 0)
        progress.setCancelButtonText("取消" if cancellable else "")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        progress.show()
        self._bring_to_front(progress)  # type: ignore[arg-type]
        return progress

    def _verify_and_install(self, path: Path, manifest: dict[str, Any]) -> None:
        """Hash the large installer off-thread, then resume canonical handoff on Qt."""

        if self._verify_thread is not None and self._verify_thread.is_alive():
            return
        self._set_progress_phase(
            "步骤 3/6 · 下载完成，正在校验更新包完整性…",
            cancellable=False,
        )
        manifest_copy = dict(manifest)
        path_text = str(path)

        def _worker() -> None:
            digest = canonical._sha256_file(Path(path_text))
            self._checksum_ready.emit(path_text, digest, manifest_copy)

        self._verify_thread = threading.Thread(
            target=_worker,
            name="listing-studio-update-sha256",
            daemon=True,
        )
        self._verify_thread.start()

    def _finish_verified_install(
        self,
        path_text: str,
        digest: str,
        manifest_obj: object,
    ) -> None:
        self._verify_thread = None
        manifest = dict(manifest_obj) if isinstance(manifest_obj, dict) else {}
        path = Path(path_text)
        if digest.lower() != str(manifest.get("installer_sha256") or "").lower():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                canonical.QMessageBox.Icon.Critical,
                "更新文件 SHA-256 校验失败，已取消安装。",
            )
            return

        version = str(manifest.get("version") or "").strip().lstrip("v")
        if not version or not canonical._write_update_marker(version):
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                canonical.QMessageBox.Icon.Critical,
                "无法写入更新交接状态，已取消安装。",
                informative="主程序保持打开；请检查 AppData 写入权限后重试。",
            )
            return

        self._set_progress_phase(
            "步骤 4/6 · 校验通过，正在关闭 Makro Browser…",
            cancellable=False,
        )
        manager = getattr(self.window, "_managed_makro_browser", None)
        poll_timer = getattr(manager, "_poll_timer", None)
        if poll_timer is not None:
            try:
                poll_timer.stop()
            except RuntimeError:
                pass

        if self._browser_thread is not None and self._browser_thread.is_alive():
            return
        manifest_copy = dict(manifest)

        def _browser_worker() -> None:
            result = close_managed_browser(
                port=DEFAULT_CDP_PORT,
                log_path=canonical.updater_log_path(),
            )
            self._browser_close_ready.emit(str(path), manifest_copy, result)

        self._browser_thread = threading.Thread(
            target=_browser_worker,
            name="listing-studio-update-browser-close",
            daemon=True,
        )
        self._browser_thread.start()

    def _finish_browser_close(
        self,
        path_text: str,
        manifest_obj: object,
        browser_result: object,
    ) -> None:
        self._browser_thread = None
        manifest = dict(manifest_obj) if isinstance(manifest_obj, dict) else {}
        path = Path(path_text)
        manager = getattr(self.window, "_managed_makro_browser", None)
        poll_timer = getattr(manager, "_poll_timer", None)

        if not bool(getattr(browser_result, "ok", False)):
            try:
                canonical.update_marker_path().unlink(missing_ok=True)
            except OSError:
                pass
            if poll_timer is not None:
                try:
                    poll_timer.start()
                except RuntimeError:
                    pass
            self._last_prompted_version = None
            self._close_progress()
            detail = str(getattr(browser_result, "detail", "") or "无法确认 Makro Browser 已安全关闭")
            self._show_update_message(
                canonical.QMessageBox.Icon.Critical,
                "无法安全关闭 Makro Browser，已取消安装。",
                informative=(
                    detail
                    + "\n\nListing Studio 保持打开；不会关闭其他普通 Edge 窗口。"
                ),
            )
            return

        self._set_progress_phase(
            "步骤 4/6 · 浏览器已关闭，正在交接更新执行器…\n"
            "执行器接管后，本面板会连续显示关闭程序、安装和重启状态。",
            cancellable=False,
        )
        arguments = [
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/NORESTARTAPPLICATIONS",
        ]
        started, detail = self._handoff_installer(path, manifest, arguments)
        if not started:
            if poll_timer is not None:
                try:
                    poll_timer.start()
                except RuntimeError:
                    pass
            if manager is not None and hasattr(manager, "ensure_async"):
                try:
                    manager.ensure_async()
                except Exception:
                    pass
            try:
                canonical.update_marker_path().unlink(missing_ok=True)
            except OSError:
                pass
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                canonical.QMessageBox.Icon.Critical,
                "自动更新没有进入安装阶段。",
                informative=detail,
                details=f"Updater log: {canonical.updater_log_path()}",
            )
            return

        self._close_progress()
        QTimer.singleShot(120, QApplication.quit)


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
