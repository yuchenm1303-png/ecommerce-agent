from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import platform
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.desktop_oauth import DesktopOAuthError, run_social_oauth

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
_TELEMETRY_URL = f"{_SUPABASE_URL}/functions/v1/portal-telemetry"
_FINGERPRINT_VERSION = 1
_HTTP_TIMEOUT_SECONDS = 22
_LICENSE_RETRY_DELAYS_SECONDS = (0.8, 2.0)
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
    telemetry_token: str = ""
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
    try:
        path = _local_state_path()
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
        "telemetry_token": session.telemetry_token,
        "validated_at": session.validated_at,
        "grace_until": session.grace_until,
        "max_devices": session.max_devices,
        "active_devices": session.active_devices,
        "display_name": session.display_name,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        path = _local_state_path()
        temp = path.with_suffix(".tmp")
        temp.write_bytes(_dpapi_protect(encoded))
        temp.replace(path)
    except (OSError, ValueError) as exc:
        raise AccessError("session_persist_failed") from exc


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
        from app.velopack_runtime import installed_application_version

        return installed_application_version()
    except Exception:
        return "0.0.0"


def _connection_failure_code(exc: BaseException) -> str:
    """Keep transport failures distinguishable without exposing account data."""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror):
        return "network_dns_failure"
    if isinstance(reason, ssl.SSLError):
        return "network_tls_failure"
    if isinstance(reason, TimeoutError) or "timed out" in str(reason).casefold():
        return "network_timeout"
    return "network_unavailable"


def _request_json(
    url: str,
    payload: dict[str, Any],
    *,
    access_token: str = "",
    retry_transient: bool = False,
) -> dict[str, Any]:
    # Refresh tokens are single-use. Do NOT automatically retry a refresh whose
    # response may have been lost: it might already have rotated the token.
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "apikey": _SUPABASE_PUBLISHABLE_KEY,
        "User-Agent": f"ListingStudio/{_installed_version()}",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    retries = _LICENSE_RETRY_DELAYS_SECONDS if retry_transient else ()
    for attempt in range(len(retries) + 1):
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
            status = int(exc.code or 0)
            if status == 429 or status >= 500:
                failure = AccessNetworkError("service_unavailable", status=status)
            else:
                raise AccessError(code, status=status) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            failure = AccessNetworkError(_connection_failure_code(exc))
        if attempt >= len(retries):
            raise failure
        time.sleep(retries[attempt])
    raise RuntimeError("unreachable network retry state")


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
        retry_transient=action == "validate",
    )


def _complete_sign_in(
    auth: dict[str, Any],
    *,
    action: str = "validate",
    persist: bool = True,
) -> ApplicationAccessSession:
    """Authorize an existing device first; activate only on explicit confirmation."""
    if action not in {"validate", "activate"}:
        raise ValueError("invalid_device_action")
    access_token = str(auth.get("access_token") or "")
    refresh_token = str(auth.get("refresh_token") or "")
    if not access_token or not refresh_token:
        raise AccessError("invalid_auth")
    device_id, device_name = device_identity()
    licensed = _license_check(
        access_token,
        action=action,
        device_id=device_id,
        device_name=device_name,
    )
    session = _session_from_auth(
        auth,
        licensed,
        device_id=device_id,
        device_name=device_name,
    )
    if persist:
        _save_state(session)
    return session


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
        telemetry_token=str(license_payload.get("telemetry_token") or ""),
        validated_at=now,
        grace_until=now + grace_hours * 3600,
        offline_grace=False,
        max_devices=max(1, int(license_payload.get("max_devices") or 2)),
        active_devices=max(0, int(license_payload.get("active_devices") or 0)),
        display_name=str(license_payload.get("display_name") or ""),
    )


def _session_from_stored(
    stored: dict[str, Any],
    *,
    refresh_token: str,
    device_id: str,
    device_name: str,
    offline_grace: bool,
) -> ApplicationAccessSession:
    return ApplicationAccessSession(
        enforced=True,
        email=str(stored.get("email") or ""),
        user_id=str(stored.get("user_id") or ""),
        refresh_token=refresh_token,
        device_id=device_id,
        device_name=device_name,
        telemetry_token=str(stored.get("telemetry_token") or ""),
        validated_at=float(stored.get("validated_at") or 0.0),
        grace_until=float(stored.get("grace_until") or 0.0),
        offline_grace=offline_grace,
        max_devices=max(1, int(stored.get("max_devices") or 2)),
        active_devices=max(0, int(stored.get("active_devices") or 0)),
        display_name=str(stored.get("display_name") or ""),
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

    now = time.time()
    try:
        validated_at = float(stored.get("validated_at") or 0.0)
    except (TypeError, ValueError):
        validated_at = 0.0
    validation_age_ms = (now - validated_at) * 1000.0
    telemetry_token = str(stored.get("telemetry_token") or "")
    if (
        telemetry_token
        and validated_at > 0.0
        and 0.0 <= validation_age_ms < _REVALIDATE_INTERVAL_MS
    ):
        try:
            return _session_from_stored(
                stored,
                refresh_token=refresh_token,
                device_id=device_id,
                device_name=device_name,
                offline_grace=False,
            )
        except (TypeError, ValueError):
            pass

    current_refresh_token = refresh_token
    try:
        auth = _auth_refresh(refresh_token)
        access_token = str(auth.get("access_token") or "")
        new_refresh = str(auth.get("refresh_token") or refresh_token)
        if not access_token:
            raise AccessError("invalid_auth")
        # Persist a newly rotated single-use refresh token BEFORE the separate
        # licensing request. A transient licence outage must not strand a
        # previously authorized device with a consumed refresh token.
        if new_refresh != refresh_token:
            interim = _session_from_stored(
                stored,
                refresh_token=new_refresh,
                device_id=device_id,
                device_name=device_name,
                offline_grace=False,
            )
            _save_state(interim)
            current_refresh_token = new_refresh
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
        try:
            session = _session_from_stored(
                stored,
                refresh_token=current_refresh_token,
                device_id=device_id,
                device_name=device_name,
                offline_grace=True,
            )
        except (TypeError, ValueError):
            return None
        if session.grace_until > time.time():
            return session
        return None
    except AccessError as exc:
        # An unwritable cache is not evidence that a license or account was revoked.
        if exc.code != "session_persist_failed":
            _clear_state()
        return None


def _restore_session_responsive(app: QApplication) -> ApplicationAccessSession | None:
    """Run stored-session I/O off the GUI thread while keeping startup paintable.

    The authorization decision itself is still made by ``_restore_session`` with
    the exact same refresh, license, offline-grace and timeout rules. Only the
    execution lane changes: network/DPAPI/filesystem work runs on a worker thread
    while the main thread services Qt events for the lightweight startup surface.
    """

    finished = threading.Event()
    outcome: dict[str, Any] = {"session": None, "error": None}

    def _worker() -> None:
        try:
            outcome["session"] = _restore_session()
        except BaseException as exc:  # preserve the synchronous exception contract
            outcome["error"] = exc
        finally:
            finished.set()

    worker = threading.Thread(
        target=_worker,
        name="listing-studio-access-restore",
        daemon=True,
    )
    worker.start()
    while not finished.wait(0.016):
        app.processEvents()
    worker.join()

    error = outcome["error"]
    if error is not None:
        raise error
    session = outcome["session"]
    return session if isinstance(session, ApplicationAccessSession) else None


def _friendly_error(error: AccessError) -> str:
    return {
        "invalid_credentials": "邮箱或密码错误。",
        "email_not_confirmed": "邮箱尚未完成验证。",
        "invalid_auth": "登录状态无效，请重新登录。",
        "invalid_grant": "登录凭据已过期，请重新登录。",
        "session_persist_failed": "无法保存登录状态，请检查 Windows 用户目录的写入权限及剩余空间。",
        "service_unavailable": "授权服务暂时繁忙，原有登录记录已保留，请稍后重试。",
        "not_authorized": "该账号尚未获得 Listing Studio 使用权限。",
        "access_expired": "该账号的 Listing Studio 授权已过期。",
        "device_limit_reached": "当前账号已达到设备授权数量上限。",
        "device_revoked": "这台设备的授权已被管理员撤销。",
        "device_not_activated": "这台设备尚未激活。",
        "network_unavailable": "当前无法连接登录服务。请检查网络或稍后重试；已有设备授权不会因此解除。",
        "network_timeout": "登录服务响应超时。跨境网络可能较慢，请检查连接后重试。",
        "network_dns_failure": "登录域名解析失败。请检查 DNS 或更换网络后重试。",
        "network_tls_failure": "登录安全连接建立失败。请检查系统时间、网络证书或代理设置后重试。",
        "oauth_cancelled": "已取消快捷登录。",
        "oauth_timeout": "快捷登录等待超时，请重新尝试。",
        "oauth_callback_port_busy": "快捷登录回调端口被其他程序占用，请关闭占用程序后重试。",
        "browser_open_failed": "无法打开系统浏览器，请检查默认浏览器设置。",
        "oauth_provider_failed": "第三方登录未完成，请重新尝试。",
        "oauth_callback_invalid": "快捷登录回调无效，请重新尝试。",
    }.get(error.code, "授权验证失败，请稍后重试。")


class _LoginDialog(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.session: ApplicationAccessSession | None = None
        self.setWindowTitle("Listing Studio · Account Access")
        self.setModal(True)
        self.setFixedWidth(460)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        title = QLabel("欢迎使用 Listing Studio")
        title.setObjectName("accessTitle")
        subtitle = QLabel("账号登录与设备激活相互独立，已有设备只需重新登录。")
        subtitle.setWordWrap(True)
        subtitle.setObjectName("accessSubtitle")
        self.step_label = QLabel("01  登录账号     /     自动检查设备授权")
        self.step_label.setObjectName("accessStep")

        self._oauth_cancel_event: threading.Event | None = None
        self._oauth_finished: threading.Event | None = None
        self._oauth_outcome: dict[str, Any] = {}
        self._oauth_poll = QTimer(self)
        self._oauth_poll.setInterval(100)
        self._oauth_poll.timeout.connect(self._poll_oauth_result)

        self.google_button = QPushButton("使用 Google 继续")
        self.google_button.setObjectName("oauthProviderButton")
        self.github_button = QPushButton("使用 GitHub 继续")
        self.github_button.setObjectName("oauthProviderButton")
        self.google_button.clicked.connect(lambda: self._start_oauth("google"))
        self.github_button.clicked.connect(lambda: self._start_oauth("github"))

        oauth_buttons = QHBoxLayout()
        oauth_buttons.setSpacing(10)
        oauth_buttons.addWidget(self.google_button)
        oauth_buttons.addWidget(self.github_button)

        oauth_separator = QLabel("或使用邮箱密码")
        oauth_separator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        oauth_separator.setObjectName("accessSeparator")

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

        self.status = QLabel("已激活的设备重新登录，不会重复占用设备名额。")
        self.status.setWordWrap(True)
        self.status.setObjectName("accessStatus")

        self._pending_activation_auth: dict[str, Any] | None = None
        self.login_button = QPushButton("登录")
        self.login_button.setDefault(True)
        self.cancel_button = QPushButton("退出")
        self.login_button.clicked.connect(self._login)
        self.cancel_button.clicked.connect(self.reject)
        self.password.returnPressed.connect(self._login)
        self.email.textEdited.connect(self._reset_pending_activation)
        self.password.textEdited.connect(self._reset_pending_activation)

        buttons = QHBoxLayout()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.login_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 27)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.step_label)
        layout.addLayout(oauth_buttons)
        layout.addWidget(oauth_separator)
        layout.addLayout(form)
        layout.addWidget(self.show_password)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

        self.setStyleSheet(
            """
            QDialog { background: #111923; color: #eef5fb; }
            QLabel#accessTitle { font-size: 21px; font-weight: 700; color: #f5f9ff; }
            QLabel#accessSubtitle { color: #a6b7c7; }
            QLabel#accessStep { color: #a9ccda; font-weight: 600; font-size: 12px;
                background: #1a2b38; border: 1px solid #314a58;
                border-radius: 9px; padding: 12px 13px; }
            QLabel#accessStatus { color: #bdcbd5; background: #182631;
                border: 1px solid #30424f; border-radius: 9px; padding: 12px; }
            QLabel#accessSeparator { color: #8196a5; font-size: 12px; padding: 1px 0; }
            QLineEdit { min-height: 36px; padding: 0 10px; border: 1px solid #314150; border-radius: 8px; background: #18232e; color: #f4f8fb; }
            QLineEdit:focus { border-color: #73c8d8; }
            QPushButton { min-height: 36px; padding: 0 16px; border: 1px solid #334858; border-radius: 8px; background: #1c2a36; color: #eaf4fb; }
            QPushButton:default { background: #236f83; border-color: #74d3e9; font-weight: 700; }
            QPushButton:default:hover { background: #2d8498; border-color: #91e2f4; }
            QPushButton#oauthProviderButton { min-height: 40px; background: #20303d; border-color: #3a5263; font-weight: 650; }
            QPushButton#oauthProviderButton:hover { background: #2a3d4b; border-color: #527286; }
            QPushButton:disabled { color: #6f808d; background: #18232e; border-color: #293946; }
            QCheckBox { color: #aebdca; }
            """
        )

        previous = _load_state()
        if previous.get("email"):
            self.email.setText(str(previous["email"]))

    def _reset_pending_activation(self, *_: Any) -> None:
        if self._pending_activation_auth is None:
            return
        self._pending_activation_auth = None
        self.login_button.setText("登录")
        self.step_label.setText("01  登录账号     /     自动检查设备授权")
        self.status.setStyleSheet("")
        self.status.setText("已更改登录信息，请重新登录并验证设备授权。")

    def _prepare_activation(self, auth: dict[str, Any]) -> None:
        # A missing device binding is the ONLY condition enabling this action.
        user = auth.get("user") if isinstance(auth.get("user"), dict) else {}
        email = str(user.get("email") or self.email.text().strip())
        if email:
            self.email.setText(email)
        self._pending_activation_auth = auth
        self.step_label.setText("02  首次设备激活     /     等待你的确认")
        self.login_button.setText("确认激活此设备")
        self.status.setStyleSheet(
            "QLabel#accessStatus { color: #f7dab0; background: #352a21;"
            "border: 1px solid #88603b; border-radius: 9px; padding: 12px; }"
        )
        self.status.setText(
            "此账号尚未在这台电脑激活。确认激活将占用一个设备授权名额；"
            "已绑定的其他设备不会受到影响。"
        )
        self._set_busy(False)

    def _activate_pending_device(self) -> None:
        auth = self._pending_activation_auth
        if auth is None:
            return
        self._set_busy(True)
        self.status.setText("正在激活当前设备并保存登录状态…")
        QApplication.processEvents()
        try:
            session = _complete_sign_in(auth, action="activate")
            self.session = session
            self._pending_activation_auth = None
            self.accept()
        except AccessError as exc:
            if not isinstance(exc, AccessNetworkError):
                self._pending_activation_auth = None
                self.login_button.setText("登录")
                self.step_label.setText("01  登录账号     /     自动检查设备授权")
                self.status.setStyleSheet("")
            self.status.setText(_friendly_error(exc))
        finally:
            if self.session is None:
                self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.email.setEnabled(not busy)
        self.password.setEnabled(not busy)
        self.show_password.setEnabled(not busy)
        self.google_button.setEnabled(not busy)
        self.github_button.setEnabled(not busy)
        self.login_button.setEnabled(not busy)
        self.cancel_button.setEnabled(True)

    def _cancel_oauth(self) -> None:
        if self._oauth_cancel_event is not None:
            self._oauth_cancel_event.set()
        self._oauth_poll.stop()

    def _start_oauth(self, provider: str) -> None:
        if self._oauth_finished is not None and not self._oauth_finished.is_set():
            return

        self._reset_pending_activation()
        cancel_event = threading.Event()
        finished = threading.Event()
        outcome: dict[str, Any] = {}
        self._oauth_cancel_event = cancel_event
        self._oauth_finished = finished
        self._oauth_outcome = outcome
        self._set_busy(True)

        provider_label = "Google" if provider == "google" else "GitHub"
        self.status.setText(
            f"正在打开 {provider_label} 登录。请在系统浏览器完成授权，完成后会自动返回 Listing Studio。"
        )

        def _worker() -> None:
            try:
                auth = run_social_oauth(
                    provider,
                    supabase_url=_SUPABASE_URL,
                    publishable_key=_SUPABASE_PUBLISHABLE_KEY,
                    user_agent=f"ListingStudio/{_installed_version()}",
                    cancel_event=cancel_event,
                )
                if cancel_event.is_set():
                    raise DesktopOAuthError("oauth_cancelled")

                if cancel_event.is_set():
                    raise DesktopOAuthError("oauth_cancelled")
                try:
                    session = _complete_sign_in(auth, action="validate", persist=False)
                except AccessError as exc:
                    if exc.code != "device_not_activated":
                        raise
                    outcome["pending_auth"] = auth
                else:
                    if cancel_event.is_set():
                        raise DesktopOAuthError("oauth_cancelled")
                    outcome["session"] = session
            except DesktopOAuthError as exc:
                outcome["error"] = AccessError(exc.code, status=exc.status)
            except AccessError as exc:
                outcome["error"] = exc
            except Exception:
                outcome["error"] = AccessError("oauth_provider_failed")
            finally:
                finished.set()

        threading.Thread(
            target=_worker,
            name=f"listing-studio-oauth-{provider}",
            daemon=True,
        ).start()
        self._oauth_poll.start()

    def _poll_oauth_result(self) -> None:
        finished = self._oauth_finished
        if finished is None or not finished.is_set():
            return
        self._oauth_poll.stop()

        session = self._oauth_outcome.get("session")
        pending_auth = self._oauth_outcome.get("pending_auth")
        error = self._oauth_outcome.get("error")
        self._oauth_finished = None
        self._oauth_cancel_event = None

        if isinstance(session, ApplicationAccessSession):
            try:
                _save_state(session)
            except AccessError as exc:
                self._set_busy(False)
                self.status.setText(_friendly_error(exc))
                return
            self.session = session
            self.accept()
            return

        self._set_busy(False)
        if isinstance(pending_auth, dict):
            self._prepare_activation(pending_auth)
            return
        if isinstance(error, AccessError):
            self.status.setText(_friendly_error(error))
        else:
            self.status.setText("快捷登录未完成，请重新尝试。")

    def reject(self) -> None:
        self._cancel_oauth()
        super().reject()

    def _login(self) -> None:
        if self._pending_activation_auth is not None:
            self._activate_pending_device()
            return

        email = self.email.text().strip()
        password = self.password.text()
        if not email or not password:
            self.status.setText("请输入邮箱和密码。")
            return

        self._set_busy(True)
        self.status.setText("正在登录并检查已有设备授权…")
        QApplication.processEvents()

        try:
            auth = _auth_password(email, password)
            try:
                session = _complete_sign_in(auth, action="validate")
            except AccessError as exc:
                if exc.code != "device_not_activated":
                    raise
                self._prepare_activation(auth)
                return
            self.session = session
            self.accept()
        except AccessError as exc:
            self.status.setText(_friendly_error(exc))
        finally:
            if self.session is None:
                self._set_busy(False)


def ensure_application_access(app: QApplication) -> ApplicationAccessSession | None:
    if not bool(getattr(sys, "frozen", False)):
        return ApplicationAccessSession.development()

    restored = _restore_session_responsive(app)
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
            if session.offline_grace:
                delay_ms = _OFFLINE_RETRY_INTERVAL_MS
            else:
                age_ms = max(0, int((time.time() - float(session.validated_at or 0.0)) * 1000.0))
                delay_ms = max(60_000, _REVALIDATE_INTERVAL_MS - age_ms)
            self._schedule(delay_ms)

    @property
    def download_function_url(self) -> str:
        return _DOWNLOAD_URL

    @property
    def telemetry_function_url(self) -> str:
        return _TELEMETRY_URL

    @property
    def installed_version(self) -> str:
        return _installed_version()

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
            if refresh_token != self.session.refresh_token:
                self.session.refresh_token = refresh_token
                _save_state(self.session)
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
            if exc.code == "session_persist_failed":
                self._schedule(_OFFLINE_RETRY_INTERVAL_MS)
                return False
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
