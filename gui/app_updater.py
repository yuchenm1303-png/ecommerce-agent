from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QFile, QIODevice, QObject, QProcess, QTimer, QUrl, QUrlQuery
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QProgressDialog


_REPOSITORY = "yuchenm1303-png/ecommerce-agent"
_LATEST_RELEASE_API = f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
_MANIFEST_ASSET = "update.json"
_PORTAL_URL = "https://smirel.com/download/"
_PORTAL_HOSTS = {"smirel.com", "www.smirel.com"}
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.-].*)?$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CHECK_DELAY_MS = 1800
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
    """Stable-channel updater driven only by manually published GitHub Releases."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.current_version = installed_application_version()
        self.network = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._download_file: QFile | None = None
        self._download_path: Path | None = None
        self._progress: QProgressDialog | None = None
        self._pending_manifest: dict[str, Any] | None = None

    @staticmethod
    def enabled() -> bool:
        return bool(getattr(sys, "frozen", False)) and os.getenv(
            "ECOMMERCE_AGENT_DISABLE_UPDATE_CHECK", ""
        ).strip().lower() not in {"1", "true", "yes"}

    def schedule_startup_check(self) -> None:
        if self.enabled():
            QTimer.singleShot(_CHECK_DELAY_MS, self.check_for_updates)

    def check_for_updates(self) -> None:
        if not self.enabled() or self._reply is not None:
            return
        reply = self.network.get(_request(_LATEST_RELEASE_API, self.current_version))
        self._reply = reply
        reply.finished.connect(lambda: self._release_finished(reply))

    def _release_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return
        self._reply = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
            assets = payload.get("assets", []) if isinstance(payload, dict) else []
            manifest_url = ""
            for asset in assets if isinstance(assets, list) else []:
                if isinstance(asset, dict) and asset.get("name") == _MANIFEST_ASSET:
                    manifest_url = str(asset.get("browser_download_url") or "")
                    break
            if not manifest_url:
                return
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return
        finally:
            reply.deleteLater()

        manifest_reply = self.network.get(_request(manifest_url, self.current_version))
        self._reply = manifest_reply
        manifest_reply.finished.connect(lambda: self._manifest_finished(manifest_reply))

    def _manifest_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return
        self._reply = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            manifest = json.loads(bytes(reply.readAll()).decode("utf-8"))
            if not isinstance(manifest, dict) or manifest.get("channel") != "stable":
                return
            latest = str(manifest.get("version") or "").strip()
            latest_key = _version_key(latest)
            current_key = _version_key(self.current_version)
            if latest_key is None or current_key is None or latest_key <= current_key:
                return

            checksum = str(manifest.get("installer_sha256") or "").strip().lower()
            if not _SHA256_RE.match(checksum):
                return

            delivery = str(manifest.get("delivery") or "github").strip().lower()
            manifest["version"] = latest
            manifest["installer_sha256"] = checksum

            if delivery == "portal":
                portal_url = str(manifest.get("portal_url") or _PORTAL_URL).strip()
                url = QUrl(portal_url)
                if (
                    not url.isValid()
                    or url.scheme().lower() != "https"
                    or url.host().lower() not in _PORTAL_HOSTS
                ):
                    return
                manifest["delivery"] = "portal"
                manifest["portal_url"] = portal_url
            else:
                installer_url = str(manifest.get("installer_url") or "").strip()
                url = QUrl(installer_url)
                if (
                    not url.isValid()
                    or url.scheme().lower() != "https"
                    or url.host().lower() != "github.com"
                ):
                    return
                manifest["delivery"] = "github"
                manifest["installer_url"] = installer_url

            self._pending_manifest = manifest
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return
        finally:
            reply.deleteLater()

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
            detail = f"{detail}\n\n更新安装包通过安全下载门户提供。"
        if required:
            detail = f"这是关键更新，需要更新后继续使用。\n\n{detail}"
        box.setInformativeText(detail)
        box.setDetailedText(
            f"Current: {self.current_version}\nLatest: {latest}\nChannel: stable\nDelivery: {delivery}"
        )
        action_label = "打开下载页" if delivery == "portal" else "立即更新"
        update_button = box.addButton(action_label, QMessageBox.ButtonRole.AcceptRole)
        if required:
            fallback_button = box.addButton("退出程序", QMessageBox.ButtonRole.RejectRole)
        else:
            fallback_button = box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(update_button)
        box.exec()

        if box.clickedButton() is update_button:
            if delivery == "portal":
                self._open_portal_update(manifest, required=required)
            else:
                self._download_update(manifest)
        elif required and box.clickedButton() is fallback_button:
            QApplication.quit()

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


def install_application_updater(window: QMainWindow) -> ApplicationUpdater:
    existing = getattr(window, "_application_updater", None)
    if isinstance(existing, ApplicationUpdater):
        return existing
    updater = ApplicationUpdater(window)
    window._application_updater = updater  # type: ignore[attr-defined]
    updater.schedule_startup_check()
    return updater


__all__ = [
    "ApplicationUpdater",
    "install_application_updater",
    "installed_application_version",
]
