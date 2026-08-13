from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QByteArray,
    QFile,
    QIODevice,
    QObject,
    QProcess,
    Qt,
    QTimer,
    QUrl,
    QUrlQuery,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QWidget,
)


_REPOSITORY = "yuchenm1303-png/ecommerce-agent"
_LATEST_RELEASE_API = f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
_MANIFEST_ASSET = "update.json"
_PORTAL_URL = "https://smirel.com/download/"
_PORTAL_HOSTS = {"smirel.com", "www.smirel.com"}
_PRIVATE_DOWNLOAD_HOSTS = {
    "nfzkphjbelyltrzgkdwt.supabase.co",
    "nfzkphjbelyltrzgkdwt.storage.supabase.co",
}
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.-].*)?$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CHECK_DELAY_MS = 1800
_AUTO_CHECK_INTERVAL_MS = 60 * 60 * 1000
_NETWORK_TIMEOUT_MS = 10_000


def _version_key(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match(value.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def installed_application_version() -> str:
    candidates: list[Path] = []
    if bool(getattr(sys, "frozen", False)):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "packaging" / "VERSION")
        candidates.append(Path(sys.executable).resolve().parent / "_internal" / "packaging" / "VERSION")
    else:
        candidates.append(Path(__file__).resolve().parents[1] / "packaging" / "VERSION")

    for path in candidates:
        try:
            version = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if _version_key(version) is not None:
            return version
    return "0.0.0"


def _request(url: str, version: str) -> QNetworkRequest:
    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"User-Agent", f"EcommerceAgent/{version}".encode("ascii", "ignore"))
    request.setRawHeader(b"Accept", b"application/vnd.github+json")
    request.setRawHeader(b"Cache-Control", b"no-cache")
    request.setTransferTimeout(_NETWORK_TIMEOUT_MS)
    return request


class ApplicationUpdater(QObject):
    """Stable-channel updater driven only by manually published releases."""

    def __init__(self, window: QMainWindow, *, access_controller: Any | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.access_controller = access_controller
        self.current_version = installed_application_version()
        self.network = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._download_file: QFile | None = None
        self._download_path: Path | None = None
        self._progress: QProgressDialog | None = None
        self._pending_manifest: dict[str, Any] | None = None
        self._manual_check = False
        self._last_prompted_version: str | None = None
        self._version_label: QLabel | None = None
        self._check_button: QPushButton | None = None
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(_AUTO_CHECK_INTERVAL_MS)
        self._auto_timer.timeout.connect(self.check_for_updates)
        self._install_header_controls()

    @staticmethod
    def enabled() -> bool:
        return bool(getattr(sys, "frozen", False)) and os.getenv(
            "ECOMMERCE_AGENT_DISABLE_UPDATE_CHECK", ""
        ).strip().lower() not in {"1", "true", "yes"}

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
            "QLabel#appVersionBadge {"
            " padding: 0 10px;"
            " color: rgba(255,255,255,194);"
            " background: rgba(20,24,34,70);"
            " border: 1px solid rgba(255,255,255,28);"
            " border-radius: 10px;"
            " font-size: 11px;"
            " font-weight: 650;"
            "}"
        )

        check_button = QPushButton("检查更新", self.window)
        check_button.setObjectName("checkUpdateButton")
        check_button.setFixedHeight(32)
        check_button.setToolTip("检查 Stable 更新通道")
        check_button.setStyleSheet(
            "QPushButton#checkUpdateButton {"
            " min-height: 30px; max-height: 30px;"
            " padding: 0 12px;"
            " color: rgba(255,255,255,220);"
            " background: rgba(20,24,34,62);"
            " border: 1px solid rgba(255,255,255,26);"
            " border-radius: 10px;"
            " font-size: 11px;"
            " font-weight: 650;"
            "}"
            "QPushButton#checkUpdateButton:hover {"
            " background: rgba(255,255,255,30);"
            " border-color: rgba(255,255,255,44);"
            "}"
            "QPushButton#checkUpdateButton:pressed {"
            " background: rgba(255,255,255,20);"
            "}"
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
        button = self._check_button
        if button is None:
            return
        try:
            button.setEnabled(not busy)
            button.setText("检查中…" if busy else "检查更新")
        except RuntimeError:
            pass

    def _finish_check(self, message: str | None = None, *, warning: bool = False) -> None:
        manual = self._manual_check
        self._manual_check = False
        if manual:
            self._set_manual_check_busy(False)
            if message:
                if warning:
                    QMessageBox.warning(self.window, "Software Update", message)
                else:
                    QMessageBox.information(self.window, "Software Update", message)

    def schedule_startup_check(self) -> None:
        if self.enabled():
            QTimer.singleShot(_CHECK_DELAY_MS, self.check_for_updates)
            if not self._auto_timer.isActive():
                self._auto_timer.start()

    def manual_check_for_updates(self) -> None:
        if not self.enabled():
            QMessageBox.information(
                self.window,
                "Software Update",
                f"当前版本 v{self.current_version}\n\n源码开发模式不连接 Stable 更新通道。",
            )
            return
        if self._reply is not None:
            QMessageBox.information(self.window, "Software Update", "更新检查或下载正在进行。")
            return
        self.check_for_updates(manual=True)

    def check_for_updates(self, *, manual: bool = False) -> None:
        if not self.enabled():
            if manual:
                self.manual_check_for_updates()
            return
        if self._reply is not None:
            if manual:
                QMessageBox.information(self.window, "Software Update", "更新检查或下载正在进行。")
            return

        self._manual_check = manual
        if manual:
            self._set_manual_check_busy(True)
        reply = self.network.get(_request(_LATEST_RELEASE_API, self.current_version))
        self._reply = reply
        reply.finished.connect(lambda: self._release_finished(reply))

    def _release_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return
        self._reply = None
        manifest_url = ""
        error_message: str | None = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                error_message = "检查更新失败，请检查网络后重试。"
            else:
                payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
                assets = payload.get("assets", []) if isinstance(payload, dict) else []
                for asset in assets if isinstance(assets, list) else []:
                    if isinstance(asset, dict) and asset.get("name") == _MANIFEST_ASSET:
                        manifest_url = str(asset.get("browser_download_url") or "")
                        break
                if not manifest_url:
                    error_message = "当前 Stable Release 没有有效的更新清单。"
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            error_message = "更新信息解析失败，请稍后重试。"
        finally:
            reply.deleteLater()

        if error_message:
            self._finish_check(error_message, warning=True)
            return

        manifest_reply = self.network.get(_request(manifest_url, self.current_version))
        self._reply = manifest_reply
        manifest_reply.finished.connect(lambda: self._manifest_finished(manifest_reply))

    def _manifest_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return
        self._reply = None
        manifest: dict[str, Any] | None = None
        latest = ""
        error_message: str | None = None
        up_to_date = False
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                error_message = "更新清单下载失败，请稍后重试。"
            else:
                payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
                if not isinstance(payload, dict) or payload.get("channel") != "stable":
                    error_message = "Stable 更新清单无效。"
                else:
                    latest = str(payload.get("version") or "").strip()
                    latest_key = _version_key(latest)
                    current_key = _version_key(self.current_version)
                    if latest_key is None or current_key is None:
                        error_message = "更新版本信息无效。"
                    elif latest_key <= current_key:
                        up_to_date = True
                    else:
                        checksum = str(payload.get("installer_sha256") or "").strip().lower()
                        if not _SHA256_RE.match(checksum):
                            error_message = "更新文件校验信息无效。"
                        else:
                            delivery = str(payload.get("delivery") or "github").strip().lower()
                            payload["version"] = latest
                            payload["installer_sha256"] = checksum

                            if delivery == "portal":
                                portal_url = str(payload.get("portal_url") or _PORTAL_URL).strip()
                                url = QUrl(portal_url)
                                if (
                                    not url.isValid()
                                    or url.scheme().lower() != "https"
                                    or url.host().lower() not in _PORTAL_HOSTS
                                ):
                                    error_message = "更新下载地址无效。"
                                else:
                                    payload["delivery"] = "portal"
                                    payload["portal_url"] = portal_url
                            else:
                                installer_url = str(payload.get("installer_url") or "").strip()
                                url = QUrl(installer_url)
                                if (
                                    not url.isValid()
                                    or url.scheme().lower() != "https"
                                    or url.host().lower() != "github.com"
                                ):
                                    error_message = "更新下载地址无效。"
                                else:
                                    payload["delivery"] = "github"
                                    payload["installer_url"] = installer_url

                            if error_message is None:
                                manifest = payload
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            error_message = "更新信息解析失败，请稍后重试。"
        finally:
            reply.deleteLater()

        if error_message:
            self._finish_check(error_message, warning=True)
            return
        if up_to_date:
            self._pending_manifest = None
            self._finish_check(f"当前已是最新版本 v{self.current_version}。")
            return
        if manifest is None:
            self._finish_check("没有找到可用更新。", warning=True)
            return

        manual = self._manual_check
        if not manual and latest == self._last_prompted_version:
            self._finish_check()
            return

        self._pending_manifest = manifest
        self._last_prompted_version = latest
        self._finish_check()
        self._prompt_for_update(manifest)

    def _prompt_for_update(self, manifest: dict[str, Any]) -> None:
        latest = str(manifest["version"])
        current_key = _version_key(self.current_version) or (0, 0, 0)
        min_supported = str(manifest.get("min_supported_version") or "").strip()
        minimum_key = _version_key(min_supported)
        required = bool(manifest.get("required", False)) or (
            minimum_key is not None and current_key < minimum_key
        )
        notes = str(manifest.get("notes") or "").strip()
        delivery = str(manifest.get("delivery") or "github")

        box = QMessageBox(self.window)
        box.setWindowTitle("Software Update")
        box.setIcon(QMessageBox.Icon.Warning if required else QMessageBox.Icon.Information)
        box.setText(f"发现新版本 {latest}")
        detail = notes or "包含稳定性与功能更新。"
        if delivery == "portal":
            detail = f"{detail}\n\n更新安装包将使用当前已授权账号安全下载。"
        if required:
            detail = f"这是关键更新，需要更新后继续使用。\n\n{detail}"
        box.setInformativeText(detail)
        box.setDetailedText(
            f"Current: {self.current_version}\nLatest: {latest}\nChannel: stable\nDelivery: {delivery}"
        )
        update_button = box.addButton("立即更新", QMessageBox.ButtonRole.AcceptRole)
        if required:
            fallback_button = box.addButton("退出程序", QMessageBox.ButtonRole.RejectRole)
        else:
            fallback_button = box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(update_button)
        box.exec()

        if box.clickedButton() is update_button:
            if delivery == "portal":
                self._download_portal_update(manifest, required=required)
            else:
                self._download_update(manifest)
        elif required and box.clickedButton() is fallback_button:
            QApplication.quit()

    def _download_portal_update(self, manifest: dict[str, Any], *, required: bool) -> None:
        access = self.access_controller
        token = ""
        if access is not None and hasattr(access, "bearer_token"):
            try:
                token = str(access.bearer_token() or "")
            except Exception:
                token = ""
        if not token:
            self._open_portal_update(manifest, required=required)
            return

        function_url = str(getattr(access, "download_function_url", "") or "")
        publishable_key = str(getattr(access, "publishable_key", "") or "")
        endpoint = QUrl(function_url)
        if (
            not endpoint.isValid()
            or endpoint.scheme().lower() != "https"
            or endpoint.host().lower() not in _PRIVATE_DOWNLOAD_HOSTS
            or not publishable_key.startswith("sb_publishable_")
        ):
            self._portal_failure("安全下载服务配置无效。", required=required)
            return

        request = QNetworkRequest(endpoint)
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"Authorization", f"Bearer {token}".encode("utf-8"))
        request.setRawHeader(b"apikey", publishable_key.encode("utf-8"))
        request.setRawHeader(
            b"User-Agent",
            f"EcommerceAgent/{self.current_version}".encode("ascii", "ignore"),
        )
        request.setTransferTimeout(_NETWORK_TIMEOUT_MS)

        payload = QByteArray(
            json.dumps(
                {"action": "download", "version": str(manifest["version"])},
                separators=(",", ":"),
            ).encode("utf-8")
        )
        reply = self.network.post(request, payload)
        self._reply = reply
        reply.finished.connect(
            lambda: self._portal_download_finished(reply, manifest, required=required)
        )

    def _portal_download_finished(
        self,
        reply: QNetworkReply,
        manifest: dict[str, Any],
        *,
        required: bool,
    ) -> None:
        if reply is not self._reply:
            return
        self._reply = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._portal_failure("授权下载请求失败，请稍后重试。", required=required)
                return
            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid response")
            signed_url = str(payload.get("url") or "").strip()
            remote_version = str(payload.get("version") or "").strip().lstrip("v")
            remote_sha = str(payload.get("sha256") or "").strip().lower()
            url = QUrl(signed_url)
            if (
                not url.isValid()
                or url.scheme().lower() != "https"
                or url.host().lower() not in _PRIVATE_DOWNLOAD_HOSTS
                or remote_version != str(manifest["version"]).lstrip("v")
                or remote_sha != str(manifest["installer_sha256"]).lower()
            ):
                raise ValueError("private release mismatch")
            private_manifest = dict(manifest)
            private_manifest["installer_url"] = signed_url
            private_manifest["installer_sha256"] = remote_sha
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            self._portal_failure("安全下载信息校验失败。", required=required)
            return
        finally:
            reply.deleteLater()

        self._download_update(private_manifest)

    def _portal_failure(self, message: str, *, required: bool) -> None:
        if required:
            QMessageBox.critical(self.window, "Software Update", message)
            QApplication.quit()
        else:
            QMessageBox.warning(self.window, "Software Update", message)

    def _open_portal_update(self, manifest: dict[str, Any], *, required: bool) -> None:
        url = QUrl(str(manifest.get("portal_url") or _PORTAL_URL))
        query = QUrlQuery(url)
        query.addQueryItem("version", str(manifest["version"]))
        url.setQuery(query)
        if not QDesktopServices.openUrl(url):
            QMessageBox.warning(self.window, "Software Update", "无法打开安全下载页，请访问 smirel.com/download/。")
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
            QMessageBox.warning(self.window, "Software Update", "无法创建更新安装文件。")
            return

        progress = QProgressDialog("正在下载更新…", "取消", 0, 100, self.window)
        progress.setWindowTitle("Software Update")
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        reply = self.network.get(_request(str(manifest["installer_url"]), self.current_version))
        self._reply = reply
        self._download_file = file
        self._download_path = target
        self._progress = progress

        reply.readyRead.connect(lambda: self._write_download(reply))
        reply.downloadProgress.connect(self._download_progress)
        reply.finished.connect(lambda: self._download_finished(reply, manifest))
        progress.canceled.connect(reply.abort)
        progress.show()

    def _write_download(self, reply: QNetworkReply) -> None:
        file = self._download_file
        if file is None or not file.isOpen():
            return
        data = reply.readAll()
        if data:
            file.write(data)

    def _download_progress(self, received: int, total: int) -> None:
        progress = self._progress
        if progress is None:
            return
        if total > 0:
            progress.setValue(max(0, min(100, round(received * 100 / total))))
        else:
            progress.setRange(0, 0)

    def _download_finished(self, reply: QNetworkReply, manifest: dict[str, Any]) -> None:
        if reply is not self._reply:
            return
        self._write_download(reply)
        self._reply = None

        file = self._download_file
        if file is not None and file.isOpen():
            file.close()
        self._download_file = None

        progress = self._progress
        self._progress = None
        if progress is not None:
            progress.close()
            progress.deleteLater()

        path = self._download_path
        self._download_path = None
        error = reply.error()
        reply.deleteLater()

        if error != QNetworkReply.NetworkError.NoError or path is None:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            if error != QNetworkReply.NetworkError.OperationCanceledError:
                QMessageBox.warning(self.window, "Software Update", "更新下载失败，请稍后重试。")
            return

        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digest = ""
        if digest.lower() != str(manifest["installer_sha256"]).lower():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            QMessageBox.critical(self.window, "Software Update", "更新文件校验失败，已取消安装。")
            return

        arguments = [
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/RESTARTAPPLICATIONS",
        ]
        started = QProcess.startDetached(str(path), arguments)
        ok = bool(started[0]) if isinstance(started, tuple) else bool(started)
        if not ok:
            QMessageBox.critical(self.window, "Software Update", "无法启动更新安装程序。")
            return

        QApplication.quit()


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


__all__ = [
    "ApplicationUpdater",
    "install_application_updater",
    "installed_application_version",
]
