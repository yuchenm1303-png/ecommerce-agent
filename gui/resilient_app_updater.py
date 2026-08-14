from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QFile, QIODevice, QProcess, Qt, QTimer, QUrl, QUrlQuery
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QProgressDialog

from gui.app_updater import (
    ApplicationUpdater as _BaseApplicationUpdater,
    _sha256_file,
    _update_marker_path,
    _version_key,
    _write_update_marker,
)


_REPOSITORY = "yuchenm1303-png/ecommerce-agent"
_LATEST_RELEASE_API = f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
_MANIFEST_ASSET = "update.json"
_NETWORK_TIMEOUT_MS = 20_000
_RETRY_DELAY_MS = 650
_MAX_RELEASE_ATTEMPTS = 2
_MAX_MANIFEST_ATTEMPTS = 2
_INSTALLER_HANDOFF_MS = 1_200
_DIAGNOSTIC_LOG = "updater-network.jsonl"

_RETRIABLE_ERRORS = {
    QNetworkReply.NetworkError.RemoteHostClosedError,
    QNetworkReply.NetworkError.HostNotFoundError,
    QNetworkReply.NetworkError.TimeoutError,
    QNetworkReply.NetworkError.TemporaryNetworkFailureError,
    QNetworkReply.NetworkError.NetworkSessionFailedError,
    QNetworkReply.NetworkError.UnknownNetworkError,
}


def _request(url: str, version: str, *, accept: bytes, github_api: bool = False) -> QNetworkRequest:
    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"User-Agent", f"EcommerceAgent/{version}".encode("ascii", "ignore"))
    request.setRawHeader(b"Accept", accept)
    request.setRawHeader(b"Cache-Control", b"no-cache")
    if github_api:
        request.setRawHeader(b"X-GitHub-Api-Version", b"2022-11-28")
    request.setTransferTimeout(_NETWORK_TIMEOUT_MS)
    request.setAttribute(
        QNetworkRequest.Attribute.RedirectPolicyAttribute,
        QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
    )
    return request


def _api_request(url: str, version: str) -> QNetworkRequest:
    return _request(
        url,
        version,
        accept=b"application/vnd.github+json",
        github_api=True,
    )


def _asset_request(url: str, version: str, *, github_api: bool) -> QNetworkRequest:
    return _request(
        url,
        version,
        accept=b"application/octet-stream",
        github_api=github_api,
    )


def _safe_url(value: str | QUrl) -> str:
    url = QUrl(value) if isinstance(value, str) else QUrl(value)
    if not url.isValid():
        return ""
    url.setQuery("")
    url.setFragment("")
    return url.toString()


def _diagnostic_path() -> Path:
    base = Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "ListingStudio"
    return base / _DIAGNOSTIC_LOG


def _http_status(reply: QNetworkReply) -> int | None:
    value = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _enum_code(value: Any) -> int | None:
    raw = getattr(value, "value", value)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _reply_diagnostic(
    stage: str,
    reply: QNetworkReply,
    *,
    attempt: int,
    source: str = "",
) -> dict[str, Any]:
    error = reply.error()
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stage": stage,
        "source": source,
        "attempt": attempt,
        "url": _safe_url(reply.url()),
        "http_status": _http_status(reply),
        "qt_error": _enum_code(error),
        "qt_error_name": getattr(error, "name", str(error)),
        "error": str(reply.errorString() or "").strip(),
    }


def _write_diagnostic(payload: dict[str, Any]) -> None:
    path = _diagnostic_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _failure_message(title: str, diagnostic: dict[str, Any]) -> str:
    status = diagnostic.get("http_status")
    error_name = str(diagnostic.get("qt_error_name") or "NetworkError")
    error_text = str(diagnostic.get("error") or "unknown network error")
    detail = f"{error_name}: {error_text}"
    if status:
        detail = f"HTTP {status} · {detail}"
    return (
        f"{title}\n\n"
        f"{detail}\n\n"
        f"已记录诊断：{_diagnostic_path()}"
    )


def _valid_manifest_api_url(value: str) -> bool:
    url = QUrl(value)
    return (
        url.isValid()
        and url.scheme().lower() == "https"
        and url.host().lower() == "api.github.com"
        and url.path().startswith(f"/repos/{_REPOSITORY}/releases/assets/")
    )


def _valid_manifest_browser_url(value: str) -> bool:
    url = QUrl(value)
    return (
        url.isValid()
        and url.scheme().lower() == "https"
        and url.host().lower() == "github.com"
        and url.path().startswith(f"/{_REPOSITORY}/releases/download/")
        and url.path().endswith(f"/{_MANIFEST_ASSET}")
    )


def _phase_label(label: str) -> str:
    text = str(label or "")
    if text.startswith("步骤 "):
        return text
    if text.startswith("正在验证更新权限"):
        return f"步骤 1/4 · {text}"
    if text.startswith("授权完成") or text.startswith("正在下载"):
        return f"步骤 2/4 · {text}"
    if text.startswith("下载完成") or text.startswith("正在校验"):
        return f"步骤 3/4 · {text}"
    if text.startswith("校验通过") or text.startswith("安装程序已启动"):
        return f"步骤 4/4 · {text}"
    return text


class ApplicationUpdater(_BaseApplicationUpdater):
    """Stable updater with resilient transport and a continuous foreground UX."""

    def __init__(self, window: QMainWindow, *, access_controller: Any | None = None) -> None:
        self._release_attempt = 0
        self._manifest_attempt = 0
        self._manifest_source = ""
        self._manifest_asset_api_url = ""
        self._manifest_browser_url = ""
        super().__init__(window, access_controller=access_controller)

    def _bring_to_front(self, dialog: QMessageBox | QProgressDialog) -> None:
        try:
            if self.window.isMinimized():
                self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
            dialog.raise_()
            dialog.activateWindow()
        except RuntimeError:
            pass

    def _show_update_message(
        self,
        icon: QMessageBox.Icon,
        text: str,
        *,
        informative: str = "",
        details: str = "",
    ) -> int:
        box = QMessageBox(self.window)
        box.setWindowTitle("Listing Studio 更新")
        box.setIcon(icon)
        box.setText(text)
        if informative:
            box.setInformativeText(informative)
        if details:
            box.setDetailedText(details)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setWindowModality(Qt.WindowModality.ApplicationModal)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        QTimer.singleShot(0, lambda: self._bring_to_front(box))
        return box.exec()

    def _show_completed_update(self) -> None:
        self._show_update_message(
            QMessageBox.Icon.Information,
            "更新已完成",
            informative=(
                "Listing Studio 已自动重新打开。\n\n"
                f"当前版本：v{self.current_version}"
            ),
        )

    def _finish_check(self, message: str | None = None, *, warning: bool = False) -> None:
        manual = self._manual_check
        self._manual_check = False
        if not manual:
            return
        self._set_manual_check_busy(False)
        if message:
            self._show_update_message(
                QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information,
                message,
            )

    def _ensure_progress(self, label: str, *, cancellable: bool = True) -> QProgressDialog:
        progress = self._progress
        if progress is None:
            progress = QProgressDialog(
                _phase_label(label),
                "取消" if cancellable else "",
                0,
                0,
                self.window,
            )
            progress.setWindowTitle("Listing Studio 更新")
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.setMinimumDuration(0)
            progress.setMinimumWidth(460)
            self._progress = progress
        else:
            progress.setLabelText(_phase_label(label))
            progress.setRange(0, 0)
            progress.setCancelButtonText("取消" if cancellable else "")

        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        progress.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, cancellable)
        progress.show()
        self._bring_to_front(progress)
        return progress

    def _set_progress_phase(self, label: str, *, cancellable: bool = False) -> None:
        progress = self._ensure_progress(_phase_label(label), cancellable=cancellable)
        progress.setRange(0, 0)
        progress.setValue(0)
        self._bring_to_front(progress)

    def manual_check_for_updates(self) -> None:
        if not self.enabled():
            self._show_update_message(
                QMessageBox.Icon.Information,
                f"当前版本 v{self.current_version}",
                informative="源码开发模式不连接 Stable 更新通道。",
            )
            return
        if self._reply is not None:
            self._show_update_message(
                QMessageBox.Icon.Information,
                "更新检查或下载正在进行。",
            )
            return
        self.check_for_updates(manual=True)

    def check_for_updates(self, *, manual: bool = False) -> None:
        if not self.enabled():
            if manual:
                self.manual_check_for_updates()
            return
        if self._reply is not None:
            if manual:
                self._show_update_message(
                    QMessageBox.Icon.Information,
                    "更新检查或下载正在进行。",
                )
            return

        self._manual_check = manual
        if manual:
            self._set_manual_check_busy(True)
        self._release_attempt = 0
        self._begin_release_request()

    def _begin_release_request(self) -> None:
        if self._reply is not None:
            return
        self._release_attempt += 1
        reply = self.network.get(_api_request(_LATEST_RELEASE_API, self.current_version))
        self._reply = reply
        reply.finished.connect(lambda: self._release_finished_resilient(reply))

    def _release_finished_resilient(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return

        error = reply.error()
        if error != QNetworkReply.NetworkError.NoError:
            diagnostic = _reply_diagnostic(
                "release_metadata",
                reply,
                attempt=self._release_attempt,
                source="github_api",
            )
            _write_diagnostic(diagnostic)
            self._reply = None
            reply.deleteLater()
            if error in _RETRIABLE_ERRORS and self._release_attempt < _MAX_RELEASE_ATTEMPTS:
                QTimer.singleShot(_RETRY_DELAY_MS, self._begin_release_request)
                return
            self._finish_check(
                _failure_message("检查更新失败，请检查网络后重试。", diagnostic),
                warning=True,
            )
            return

        asset_api_url = ""
        browser_url = ""
        parse_error = ""
        try:
            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
            assets = payload.get("assets", []) if isinstance(payload, dict) else []
            for asset in assets if isinstance(assets, list) else []:
                if not isinstance(asset, dict) or asset.get("name") != _MANIFEST_ASSET:
                    continue
                candidate_api = str(asset.get("url") or "").strip()
                candidate_browser = str(asset.get("browser_download_url") or "").strip()
                if _valid_manifest_api_url(candidate_api):
                    asset_api_url = candidate_api
                if _valid_manifest_browser_url(candidate_browser):
                    browser_url = candidate_browser
                break
            if not asset_api_url and not browser_url:
                parse_error = "当前 Stable Release 没有有效的更新清单。"
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            parse_error = "更新信息解析失败，请稍后重试。"
        finally:
            self._reply = None
            reply.deleteLater()

        if parse_error:
            _write_diagnostic(
                {
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "stage": "release_metadata_parse",
                    "attempt": self._release_attempt,
                    "error": parse_error,
                }
            )
            self._finish_check(parse_error, warning=True)
            return

        self._manifest_asset_api_url = asset_api_url
        self._manifest_browser_url = browser_url
        self._manifest_source = "asset_api" if asset_api_url else "browser_url"
        self._manifest_attempt = 0
        self._begin_manifest_request()

    def _begin_manifest_request(self) -> None:
        if self._reply is not None:
            return
        if self._manifest_source == "asset_api":
            url = self._manifest_asset_api_url
            github_api = True
        else:
            url = self._manifest_browser_url
            github_api = False
        if not url:
            self._finish_check("当前 Stable Release 没有有效的更新清单。", warning=True)
            return

        self._manifest_attempt += 1
        reply = self.network.get(
            _asset_request(url, self.current_version, github_api=github_api)
        )
        self._reply = reply
        reply.finished.connect(lambda: self._manifest_finished_resilient(reply))

    def _manifest_finished_resilient(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return
        error = reply.error()
        if error == QNetworkReply.NetworkError.NoError:
            # The base updater owns manifest policy/validation. Its UI callbacks
            # dispatch back into this class, so transport and presentation stay unified.
            super()._manifest_finished(reply)
            return

        diagnostic = _reply_diagnostic(
            "manifest_download",
            reply,
            attempt=self._manifest_attempt,
            source=self._manifest_source,
        )
        _write_diagnostic(diagnostic)
        self._reply = None
        reply.deleteLater()

        if error == QNetworkReply.NetworkError.OperationCanceledError:
            self._finish_check()
            return

        if error in _RETRIABLE_ERRORS and self._manifest_attempt < _MAX_MANIFEST_ATTEMPTS:
            QTimer.singleShot(_RETRY_DELAY_MS, self._begin_manifest_request)
            return

        if self._manifest_source == "asset_api" and self._manifest_browser_url:
            self._manifest_source = "browser_url"
            self._manifest_attempt = 0
            QTimer.singleShot(_RETRY_DELAY_MS, self._begin_manifest_request)
            return

        self._finish_check(
            _failure_message("更新清单下载失败，请稍后重试。", diagnostic),
            warning=True,
        )

    def _prompt_for_update(self, manifest: dict[str, Any]) -> None:
        latest = str(manifest["version"])
        current_key = _version_key(self.current_version) or (0, 0, 0)
        min_supported = str(manifest.get("min_supported_version") or "").strip()
        minimum_key = _version_key(min_supported)
        required = bool(manifest.get("required", False)) or (
            minimum_key is not None and current_key < minimum_key
        )
        notes = str(manifest.get("notes") or "").strip()
        delivery = str(manifest.get("delivery") or "portal")

        box = QMessageBox(self.window)
        box.setWindowTitle("Listing Studio 更新")
        box.setIcon(QMessageBox.Icon.Warning if required else QMessageBox.Icon.Information)
        box.setText(f"发现新版本 v{latest.lstrip('v')}")
        detail = notes or "包含稳定性与功能更新。"
        detail = (
            f"{detail}\n\n"
            "接下来会持续显示更新状态：\n"
            "1. 准备下载权限\n"
            "2. 下载更新包\n"
            "3. 校验文件完整性\n"
            "4. 启动安装并自动重启\n\n"
            "更新不会自动提交商品，也不会触发 Send to QC。\n"
            "安装阶段会关闭 Listing Studio，请先结束正在执行的上架任务。"
        )
        if delivery == "portal":
            detail = f"{detail}\n\n更新包会使用当前已授权账号进行安全下载。"
        if required:
            detail = f"这是关键更新，需要更新后继续使用。\n\n{detail}"
        box.setInformativeText(detail)
        box.setDetailedText(
            f"Current: {self.current_version}\n"
            f"Latest: {latest}\n"
            "Channel: stable\n"
            f"Delivery: {delivery}"
        )
        update_button = box.addButton("立即更新", QMessageBox.ButtonRole.AcceptRole)
        fallback_button = box.addButton(
            "退出程序" if required else "稍后",
            QMessageBox.ButtonRole.RejectRole,
        )
        box.setDefaultButton(update_button)
        box.setWindowModality(Qt.WindowModality.ApplicationModal)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if required:
            box.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        QTimer.singleShot(0, lambda: self._bring_to_front(box))
        box.exec()

        if box.clickedButton() is update_button:
            if delivery == "portal":
                self._download_portal_update(manifest, required=required)
            else:
                self._download_update(manifest)
            return

        if required:
            QApplication.quit()
        elif box.clickedButton() is fallback_button:
            return

    def _portal_failure(self, message: str, *, required: bool) -> None:
        self._last_prompted_version = None
        self._close_progress()
        self._show_update_message(
            QMessageBox.Icon.Critical if required else QMessageBox.Icon.Warning,
            message,
        )
        if required:
            QApplication.quit()

    def _open_portal_update(self, manifest: dict[str, Any], *, required: bool) -> None:
        box = QMessageBox(self.window)
        box.setWindowTitle("Listing Studio 更新")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("需要转到安全下载页继续更新")
        box.setInformativeText(
            "当前登录状态暂时无法完成内置授权下载。\n\n"
            + (
                "点击下面的按钮后会打开官方安全下载页，Listing Studio 随后退出。"
                if required
                else "点击下面的按钮后会打开官方安全下载页，Listing Studio 会保持打开。"
            )
        )
        open_button = box.addButton(
            "打开下载页并退出" if required else "打开下载页",
            QMessageBox.ButtonRole.AcceptRole,
        )
        cancel_button = box.addButton(
            "退出程序" if required else "稍后",
            QMessageBox.ButtonRole.RejectRole,
        )
        box.setDefaultButton(open_button)
        box.setWindowModality(Qt.WindowModality.ApplicationModal)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if required:
            box.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        QTimer.singleShot(0, lambda: self._bring_to_front(box))
        box.exec()

        if box.clickedButton() is not open_button:
            if required and box.clickedButton() is cancel_button:
                QApplication.quit()
            return

        url = QUrl(str(manifest.get("portal_url") or "https://smirel.com/download/"))
        query = QUrlQuery(url)
        query.addQueryItem("version", str(manifest["version"]))
        url.setQuery(query)
        if not QDesktopServices.openUrl(url):
            self._show_update_message(
                QMessageBox.Icon.Warning,
                "无法打开安全下载页。",
                informative="请稍后重新检查更新。",
            )
            return
        if required:
            QApplication.quit()

    def _download_update(self, manifest: dict[str, Any]) -> None:
        version = str(manifest["version"])
        checksum = str(manifest["installer_sha256"])
        target = Path(tempfile.gettempdir()) / (
            f"EcommerceAgent-Setup-{version}-{checksum[:10]}.exe"
        )
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass

        file = QFile(str(target))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Truncate):
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                QMessageBox.Icon.Warning,
                "无法创建更新安装文件。",
            )
            return

        progress = self._ensure_progress(
            f"步骤 2/4 · 正在下载 v{version.lstrip('v')} 更新包…",
            cancellable=True,
        )
        progress.setRange(0, 100)
        progress.setValue(0)

        installer_url = str(manifest["installer_url"])
        github_api = QUrl(installer_url).host().lower() == "api.github.com"
        reply = self.network.get(
            _asset_request(installer_url, self.current_version, github_api=github_api)
        )
        self._reply = reply
        self._download_file = file
        self._download_path = target

        reply.readyRead.connect(lambda: self._write_download(reply))
        reply.downloadProgress.connect(self._download_progress)
        reply.finished.connect(lambda: self._download_finished_resilient(reply, manifest))
        progress.canceled.connect(reply.abort)
        progress.show()
        self._bring_to_front(progress)

    def _download_progress(self, received: int, total: int) -> None:
        progress = self._progress
        if progress is None:
            return
        if total > 0:
            percent = max(0, min(100, round(received * 100 / total)))
            received_mb = received / 1024 / 1024
            total_mb = total / 1024 / 1024
            progress.setRange(0, 100)
            progress.setValue(percent)
            progress.setLabelText(
                "步骤 2/4 · 正在下载更新… "
                f"{percent}%   ·   {received_mb:.1f} / {total_mb:.1f} MB"
            )
        else:
            progress.setRange(0, 0)
            progress.setLabelText("步骤 2/4 · 正在下载更新…")

    def _download_finished_resilient(
        self,
        reply: QNetworkReply,
        manifest: dict[str, Any],
    ) -> None:
        if reply is not self._reply:
            return
        self._write_download(reply)
        self._reply = None

        file = self._download_file
        if file is not None and file.isOpen():
            file.close()
        self._download_file = None

        path = self._download_path
        self._download_path = None
        error = reply.error()
        diagnostic: dict[str, Any] | None = None
        if error not in {
            QNetworkReply.NetworkError.NoError,
            QNetworkReply.NetworkError.OperationCanceledError,
        }:
            diagnostic = _reply_diagnostic(
                "installer_download",
                reply,
                attempt=1,
                source="release_asset",
            )
            _write_diagnostic(diagnostic)
        reply.deleteLater()

        if error != QNetworkReply.NetworkError.NoError or path is None:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._close_progress()
            if error == QNetworkReply.NetworkError.OperationCanceledError:
                return
            self._last_prompted_version = None
            self._show_update_message(
                QMessageBox.Icon.Warning,
                _failure_message(
                    "更新下载失败，请稍后重试。",
                    diagnostic
                    or {
                        "qt_error_name": "NetworkError",
                        "error": "download did not produce a valid file",
                    },
                ),
            )
            return

        self._set_progress_phase(
            "步骤 3/4 · 下载完成，正在校验更新包完整性…",
            cancellable=False,
        )
        QTimer.singleShot(0, lambda: self._verify_and_install(path, manifest))

    def _verify_and_install(self, path: Path, manifest: dict[str, Any]) -> None:
        digest = _sha256_file(path)
        if digest.lower() != str(manifest["installer_sha256"]).lower():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                QMessageBox.Icon.Critical,
                "更新文件校验失败，已取消安装。",
            )
            return

        version = str(manifest["version"]).strip().lstrip("v")
        self._set_progress_phase(
            "步骤 4/4 · 校验通过，正在启动安装程序…\n"
            "接下来会显示安装进度，完成后 Listing Studio 将自动重新打开。",
            cancellable=False,
        )
        _write_update_marker(version)

        arguments = [
            "/SILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/NORESTARTAPPLICATIONS",
        ]
        started = QProcess.startDetached(str(path), arguments)
        ok = bool(started[0]) if isinstance(started, tuple) else bool(started)
        if not ok:
            try:
                _update_marker_path().unlink(missing_ok=True)
            except OSError:
                pass
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                QMessageBox.Icon.Critical,
                "无法启动更新安装程序。",
            )
            return

        self._set_progress_phase(
            "步骤 4/4 · 安装程序已启动。\n"
            "Listing Studio 即将关闭；安装完成后会自动重新打开。",
            cancellable=False,
        )
        QTimer.singleShot(_INSTALLER_HANDOFF_MS, QApplication.quit)


def install_application_updater(
    window: QMainWindow,
    *,
    access_controller: Any | None = None,
) -> ApplicationUpdater:
    existing = getattr(window, "_application_updater", None)
    if isinstance(existing, _BaseApplicationUpdater):
        return existing  # type: ignore[return-value]
    updater = ApplicationUpdater(window, access_controller=access_controller)
    window._application_updater = updater  # type: ignore[attr-defined]
    updater.schedule_startup_check()
    return updater


__all__ = [
    "ApplicationUpdater",
    "install_application_updater",
]
