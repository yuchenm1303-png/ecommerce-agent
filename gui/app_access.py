from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import platform
import socket
import sys
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


_SUPABASE_URL = "https://nfzkphjbelyltrzgkdwt.supabase.co"
_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_tE8SeTOj-ERgmqvP4l5Hiw_arCxCJLa"
_AUTH_PASSWORD_URL = f"{_SUPABASE_URL}/auth/v1/token?grant_type=password"
_AUTH_REFRESH_URL = f"{_SUPABASE_URL}/auth/v1/token?grant_type=refresh_token"
_LICENSE_URL = f"{_SUPABASE_URL}/functions/v1/portal-license"
_DOWNLOAD_URL = f"{_SUPABASE_URL}/functions/v1/portal-download"
_FINGERPRINT_VERSION = 1
_HTTP_TIMEOUT_SECONDS = 12
_REVALIDATE_INTERVAL_MS = 6 * 60 * 60 * 1000
_OFFLINE_RETRY_INTERVAL_MS = 30 * 60 * 1000


class AccessError(RuntimeError):
    def __init__(self, code: str, *, status: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class AccessNetworkError(AccessError):
    pass


@dataclass
class ApplicationAccessSession:
    enforced: bool
    email: str = ""
    user_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    device_id: str = ""
    device_name: str = ""
    validated_at: float = 0.0
    grace_until: float = 0.0
    offline_grace: bool = False
    max_devices: int = 0
    active_devices: int = 0
    display_name: str = ""

    @classmethod
    def development(cls) -> "ApplicationAccessSession":
        return cls(enforced=False)


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _local_state_path() -> Path:
    root = os.getenv("LOCALAPPDATA")
    base = Path(root) if root else Path.home() / "AppData" / "Local"
    path = base / "ListingStudio" / "access.dat"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        return base64.b64encode(data)
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source = ctypes.create_string_buffer(data)
    source_blob = _DATA_BLOB(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    target_blob = _DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(source_blob),
        "Listing Studio Access",
        None,
        None,
        None,
        0x01,
        ctypes.byref(target_blob),
    )
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(target_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        return base64.b64decode(data)
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source = ctypes.create_string_buffer(data)
    source_blob = _DATA_BLOB(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    target_blob = _DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(source_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(target_blob),
    )
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(target_blob.pbData)


def _load_state() -> dict[str, Any]:
    path = _local_state_path()
    try:
        raw = _dpapi_unprotect(path.read_bytes())
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(session: ApplicationAccessSession) -> None:
    if not session.enforced or not session.refresh_token:
        return
    payload = {
        "email": session.email,
        "user_id": session.user_id,
        "refresh_token": session.refresh_token,
        "device_id": session.device_id,
        "device_name": session.device_name,
        "validated_at": session.validated_at,
        "grace_until": session.grace_until,
        "max_devices": session.max_devices,
        "active_devices": session.active_devices,
        "display_name": session.display_name,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path = _local_state_path()
    temp = path.with_suffix(".tmp")
    temp.write_bytes(_dpapi_protect(encoded))
    temp.replace(path)


def _clear_state() -> None:
    try:
        _local_state_path().unlink(missing_ok=True)
    except OSError:
        pass


def _machine_guid() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
            ) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                if value:
                    return str(value).strip()
        except OSError:
            pass
    return f"{platform.node()}|{platform.machine()}|{platform.system()}"


def device_identity() -> tuple[str, str]:
    raw = f"listing-studio:v{_FINGERPRINT_VERSION}:{_machine_guid()}"
    device_id = hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()
    device_name = socket.gethostname().strip() or "Windows PC"
    return device_id, device_name[:160]


def _installed_version() -> str:
    try:
        from gui.app_updater import installed_application_version

        return installed_application_version()
    except Exception:
        return "0.0.0"


def _request_json(
    url: str,
    payload: dict[str, Any],
    *,
    access_token: str = "",
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "apikey": _SUPABASE_PUBLISHABLE_KEY,
        "User-Agent": f"ListingStudio/{_installed_version()}",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            return parsed if isinstance(parsed, dict) else {}
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {}
        code = str(parsed.get("error") or parsed.get("error_code") or "request_failed")
        raise AccessError(code, status=int(exc.code or 0)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AccessNetworkError("network_unavailable") from exc


def _auth_password(email: str, password: str) -> dict[str, Any]:
    return _request_json(_AUTH_PASSWORD_URL, {"email": email, "password": password})


def _auth_refresh(refresh_token: str) -> dict[str, Any]:
    return _request_json(_AUTH_REFRESH_URL, {"refresh_token": refresh_token})


def _license_check(
    access_token: str,
    *,
    action: str,
    device_id: str,
    device_name: str,
) -> dict[str, Any]:
    return _request_json(
        _LICENSE_URL,
        {
            "action": action,
            "device_id": device_id,
            "device_name": device_name,
            "fingerprint_version": _FINGERPRINT_VERSION,
            "app_version": _installed_version(),
        },
        access_token=access_token,
    )


def _session_from_auth(
    auth: dict[str, Any],
    license_payload: dict[str, Any],
    *,
    device_id: str,
    device_name: str,
) -> ApplicationAccessSession:
    now = time.time()
    grace_hours = max(0, int(license_payload.get("grace_period_hours") or 72))
    user = auth.get("user") if isinstance(auth.get("user"), dict) else {}
    return ApplicationAccessSession(
        enforced=True,
        email=str(license_payload.get("email") or user.get("email") or ""),
        user_id=str(license_payload.get("user_id") or user.get("id") or ""),
        access_token=str(auth.get("access_token") or ""),
        refresh_token=str(auth.get("refresh_token") or ""),
        device_id=device_id,
        device_name=device_name,
        validated_at=now,
        grace_until=now + grace_hours * 3600,
        offline_grace=False,
        max_devices=max(1, int(license_payload.get("max_devices") or 2)),
        active_devices=max(0, int(license_payload.get("active_devices") or 0)),
        display_name=str(license_payload.get("display_name") or ""),
    )


def _restore_session() -> ApplicationAccessSession | None:
    stored = _load_state()
    refresh_token = str(stored.get("refresh_token") or "")
    if not refresh_token:
        return None

    device_id, device_name = device_identity()
    stored_device_id = str(stored.get("device_id") or "")
    if stored_device_id and stored_device_id != device_id:
        _clear_state()
        return None

    try:
        auth = _auth_refresh(refresh_token)
        access_token = str(auth.get("access_token") or "")
        new_refresh = str(auth.get("refresh_token") or refresh_token)
        if not access_token:
            raise AccessError("invalid_auth")
        auth["refresh_token"] = new_refresh
        licensed = _license_check(
            access_token,
            action="validate",
            device_id=device_id,
            device_name=device_name,
        )
        session = _session_from_auth(
            auth,
            licensed,
            device_id=device_id,
            device_name=device_name,
        )
        _save_state(session)
        return session
    except AccessNetworkError:
        grace_until = float(stored.get("grace_until") or 0.0)
        if grace_until > time.time():
            return ApplicationAccessSession(
                enforced=True,
                email=str(stored.get("email") or ""),
                user_id=str(stored.get("user_id") or ""),
                refresh_token=refresh_token,
                device_id=device_id,
                device_name=device_name,
                validated_at=float(stored.get("validated_at") or 0.0),
                grace_until=grace_until,
                offline_grace=True,
                max_devices=max(1, int(stored.get("max_devices") or 2)),
                active_devices=max(0, int(stored.get("active_devices") or 0)),
                display_name=str(stored.get("display_name") or ""),
            )
        return None
    except AccessError:
        _clear_state()
        return None


def _friendly_error(error: AccessError) -> str:
    return {
        "invalid_credentials": "邮箱或密码错误。",
        "email_not_confirmed": "邮箱尚未完成验证。",
        "invalid_auth": "登录状态无效，请重新登录。",
        "not_authorized": "该账号尚未获得 Listing Studio 使用权限。",
        "access_expired": "该账号的 Listing Studio 授权已过期。",
        "device_limit_reached": "当前账号已达到设备授权数量上限。",
        "device_revoked": "这台设备的授权已被管理员撤销。",
        "device_not_activated": "这台设备尚未激活。",
        "network_unavailable": "无法连接授权服务器，请检查网络后重试。",
    }.get(error.code, "授权验证失败，请稍后重试。")


class _LoginDialog(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.session: ApplicationAccessSession | None = None
        self.setWindowTitle("Listing Studio · Account Access")
        self.setModal(True)
        self.setFixedWidth(430)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        title = QLabel("Listing Studio")
        title.setObjectName("accessTitle")
        subtitle = QLabel("登录已授权账号后才能使用正式安装版。")
        subtitle.setWordWrap(True)
        subtitle.setObjectName("accessSubtitle")

        self.email = QLineEdit()
        self.email.setPlaceholderText("name@example.com")
        self.email.setClearButtonEnabled(True)
        self.password = QLineEdit()
        self.password.setPlaceholderText("密码")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)

        self.show_password = QCheckBox("显示密码")
        self.show_password.toggled.connect(
            lambda checked: self.password.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("邮箱", self.email)
        form.addRow("密码", self.password)

        self.status = QLabel("账号权限与设备授权会同时验证。")
        self.status.setWordWrap(True)
        self.status.setObjectName("accessStatus")

        self.login_button = QPushButton("登录并激活此设备")
        self.login_button.setDefault(True)
        self.cancel_button = QPushButton("退出")
        self.login_button.clicked.connect(self._login)
        self.cancel_button.clicked.connect(self.reject)
        self.password.returnPressed.connect(self._login)

        buttons = QHBoxLayout()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.login_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(form)
        layout.addWidget(self.show_password)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

        self.setStyleSheet(
            """
            QDialog { background: #111923; color: #eef5fb; }
            QLabel#accessTitle { font-size: 24px; font-weight: 700; }
            QLabel#accessSubtitle, QLabel#accessStatus { color: #9eb0bf; }
            QLineEdit { min-height: 36px; padding: 0 10px; border: 1px solid #314150; border-radius: 8px; background: #18232e; color: #f4f8fb; }
            QLineEdit:focus { border-color: #73c8d8; }
            QPushButton { min-height: 36px; padding: 0 16px; border: 1px solid #334858; border-radius: 8px; background: #1c2a36; color: #eaf4fb; }
            QPushButton:default { background: #2f7282; border-color: #63b8ca; }
            QCheckBox { color: #aebdca; }
            """
        )

        previous = _load_state()
        if previous.get("email"):
            self.email.setText(str(previous["email"]))

    def _login(self) -> None:
        email = self.email.text().strip()
        password = self.password.text()
        if not email or not password:
            self.status.setText("请输入邮箱和密码。")
            return

        self.login_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.status.setText("正在验证账号与设备权限…")
        QApplication.processEvents()

        device_id, device_name = device_identity()
        try:
            auth = _auth_password(email, password)
            access_token = str(auth.get("access_token") or "")
            if not access_token:
                raise AccessError("invalid_auth")
            licensed = _license_check(
                access_token,
                action="activate",
                device_id=device_id,
                device_name=device_name,
            )
            session = _session_from_auth(
                auth,
                licensed,
                device_id=device_id,
                device_name=device_name,
            )
            if not session.refresh_token:
                raise AccessError("invalid_auth")
            _save_state(session)
            self.session = session
            self.accept()
            return
        except AccessError as exc:
            self.status.setText(_friendly_error(exc))
        finally:
            self.login_button.setEnabled(True)
            self.cancel_button.setEnabled(True)


def ensure_application_access(app: QApplication) -> ApplicationAccessSession | None:
    del app
    if not bool(getattr(sys, "frozen", False)):
        return ApplicationAccessSession.development()

    restored = _restore_session()
    if restored is not None:
        return restored

    dialog = _LoginDialog()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.session


class ApplicationAccessController(QObject):
    def __init__(self, parent: QWidget, session: ApplicationAccessSession) -> None:
        super().__init__(parent)
        self.window = parent
        self.session = session
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.revalidate)
        if session.enforced:
            self._schedule(
                _OFFLINE_RETRY_INTERVAL_MS if session.offline_grace else _REVALIDATE_INTERVAL_MS
            )

    @property
    def download_function_url(self) -> str:
        return _DOWNLOAD_URL

    @property
    def publishable_key(self) -> str:
        return _SUPABASE_PUBLISHABLE_KEY

    def bearer_token(self) -> str:
        if not self.session.enforced:
            return ""
        if self.session.access_token and not self._token_near_expiry(self.session.access_token):
            return self.session.access_token
        if self._refresh_online(show_failure=False):
            return self.session.access_token
        return ""

    @staticmethod
    def _token_near_expiry(token: str) -> bool:
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
            return float(data.get("exp") or 0) <= time.time() + 90
        except Exception:
            return True

    def _schedule(self, delay_ms: int) -> None:
        self.timer.start(max(60_000, int(delay_ms)))

    def _refresh_online(self, *, show_failure: bool) -> bool:
        try:
            auth = _auth_refresh(self.session.refresh_token)
            access_token = str(auth.get("access_token") or "")
            refresh_token = str(auth.get("refresh_token") or self.session.refresh_token)
            if not access_token:
                raise AccessError("invalid_auth")
            licensed = _license_check(
                access_token,
                action="validate",
                device_id=self.session.device_id,
                device_name=self.session.device_name,
            )
            refreshed = _session_from_auth(
                {**auth, "refresh_token": refresh_token},
                licensed,
                device_id=self.session.device_id,
                device_name=self.session.device_name,
            )
            self.session = refreshed
            _save_state(refreshed)
            self._schedule(_REVALIDATE_INTERVAL_MS)
            return True
        except AccessNetworkError:
            if self.session.grace_until > time.time():
                self.session.offline_grace = True
                self._schedule(_OFFLINE_RETRY_INTERVAL_MS)
                return False
            if show_failure:
                self._deny("授权服务器暂时不可用，且离线宽限期已结束。")
            return False
        except AccessError as exc:
            if show_failure:
                self._deny(_friendly_error(exc))
            return False

    def revalidate(self) -> None:
        if not self.session.enforced:
            return
        self._refresh_online(show_failure=True)

    def _deny(self, message: str) -> None:
        _clear_state()
        QMessageBox.critical(
            self.window,
            "Listing Studio · 授权失效",
            f"{message}\n\n程序将退出。重新获得授权后再次启动即可。",
        )
        QApplication.quit()


def install_application_access(
    window: QWidget,
    session: ApplicationAccessSession,
) -> ApplicationAccessController:
    existing = getattr(window, "_application_access", None)
    if isinstance(existing, ApplicationAccessController):
        return existing
    controller = ApplicationAccessController(window, session)
    window._application_access = controller  # type: ignore[attr-defined]
    return controller


__all__ = [
    "ApplicationAccessController",
    "ApplicationAccessSession",
    "device_identity",
    "ensure_application_access",
    "install_application_access",
]
