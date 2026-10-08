from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable


_LOOPBACK_HOST = "127.0.0.1"
_LOOPBACK_PORT = 47843
_LOOPBACK_PATH = "/oauth/callback"
_OAUTH_TIMEOUT_SECONDS = 300
_HTTP_TIMEOUT_SECONDS = 22
_ALLOWED_PROVIDERS = {"google", "github"}


class DesktopOAuthError(RuntimeError):
    def __init__(self, code: str, *, status: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class OAuthCallback:
    code: str = ""
    error: str = ""
    error_description: str = ""


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def create_pkce_pair() -> tuple[str, str]:
    """Return a RFC 7636 verifier and S256 challenge."""

    verifier = secrets.token_urlsafe(48)
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(
    supabase_url: str,
    provider: str,
    redirect_uri: str,
    code_challenge: str,
) -> str:
    provider = str(provider or "").strip().casefold()
    if provider not in _ALLOWED_PROVIDERS:
        raise DesktopOAuthError("oauth_provider_invalid")

    query = urllib.parse.urlencode(
        {
            "provider": provider,
            "redirect_to": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "s256",
        }
    )
    return f"{supabase_url.rstrip('/')}/auth/v1/authorize?{query}"


def exchange_pkce_code(
    supabase_url: str,
    publishable_key: str,
    code: str,
    verifier: str,
    *,
    user_agent: str,
    timeout: int = _HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    body = json.dumps(
        {"auth_code": str(code), "code_verifier": str(verifier)},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{supabase_url.rstrip('/')}/auth/v1/token?grant_type=pkce",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "apikey": publishable_key,
            "User-Agent": user_agent,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        code_value = str(
            payload.get("error_code")
            or payload.get("code")
            or payload.get("error")
            or "oauth_exchange_failed"
        )
        raise DesktopOAuthError(code_value, status=int(exc.code or 0)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DesktopOAuthError("network_unavailable") from exc

    if not isinstance(payload, dict):
        raise DesktopOAuthError("invalid_auth")
    if not payload.get("access_token") or not payload.get("refresh_token"):
        raise DesktopOAuthError("invalid_auth")
    return payload


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "ListingStudioOAuth/1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        receiver: LoopbackOAuthReceiver = self.server.receiver  # type: ignore[attr-defined]
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != _LOOPBACK_PATH:
            self.send_error(404)
            return

        query = urllib.parse.parse_qs(parsed.query)
        result = OAuthCallback(
            code=str((query.get("code") or [""])[0]),
            error=str((query.get("error") or [""])[0]),
            error_description=str((query.get("error_description") or [""])[0]),
        )
        receiver.complete(result)

        ok = bool(result.code) and not result.error
        title = "登录已完成" if ok else "登录未完成"
        copy = (
            "Listing Studio 已收到登录结果。现在可以关闭此页面并返回程序。"
            if ok
            else "Listing Studio 已收到登录结果。请返回程序查看提示。"
        )
        html = (
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title>"
            "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;"
            "background:#111923;color:#eef5fb;font:16px system-ui,-apple-system,sans-serif}"
            ".card{max-width:520px;margin:24px;padding:28px 30px;border-radius:16px;"
            "background:#1b2935;border:1px solid #334858;box-shadow:0 20px 60px #0005}"
            "h1{margin:0 0 10px;font-size:26px}p{margin:0;color:#a9bac8;line-height:1.7}</style>"
            f"</head><body><main class='card'><h1>{title}</h1><p>{copy}</p></main></body></html>"
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _LoopbackHTTPServer(http.server.HTTPServer):
    allow_reuse_address = False


class LoopbackOAuthReceiver:
    def __init__(self, *, port: int = _LOOPBACK_PORT) -> None:
        try:
            self.server = _LoopbackHTTPServer((_LOOPBACK_HOST, int(port)), _CallbackHandler)
        except OSError as exc:
            if isinstance(exc, socket.error):
                raise DesktopOAuthError("oauth_callback_port_busy") from exc
            raise
        self.server.timeout = 0.25
        self.server.receiver = self  # type: ignore[attr-defined]
        self._result: OAuthCallback | None = None
        self._result_lock = threading.Lock()

    @property
    def redirect_uri(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}{_LOOPBACK_PATH}"

    def complete(self, callback: OAuthCallback) -> None:
        with self._result_lock:
            if self._result is None:
                self._result = callback

    def wait(
        self,
        cancel_event: threading.Event,
        *,
        timeout_seconds: int = _OAUTH_TIMEOUT_SECONDS,
    ) -> OAuthCallback:
        deadline = time.monotonic() + max(1, int(timeout_seconds))
        try:
            while not cancel_event.is_set() and time.monotonic() < deadline:
                with self._result_lock:
                    if self._result is not None:
                        return self._result
                self.server.handle_request()
            if cancel_event.is_set():
                raise DesktopOAuthError("oauth_cancelled")
            raise DesktopOAuthError("oauth_timeout")
        finally:
            self.server.server_close()


def run_social_oauth(
    provider: str,
    *,
    supabase_url: str,
    publishable_key: str,
    user_agent: str,
    cancel_event: threading.Event,
    open_browser: Callable[[str], bool] = webbrowser.open,
    callback_port: int = _LOOPBACK_PORT,
    timeout_seconds: int = _OAUTH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    verifier, challenge = create_pkce_pair()
    receiver = LoopbackOAuthReceiver(port=callback_port)
    authorize_url = build_authorize_url(
        supabase_url,
        provider,
        receiver.redirect_uri,
        challenge,
    )

    if cancel_event.is_set():
        receiver.server.server_close()
        raise DesktopOAuthError("oauth_cancelled")

    try:
        opened = bool(open_browser(authorize_url))
    except Exception as exc:
        receiver.server.server_close()
        raise DesktopOAuthError("browser_open_failed") from exc
    if not opened:
        receiver.server.server_close()
        raise DesktopOAuthError("browser_open_failed")

    callback = receiver.wait(cancel_event, timeout_seconds=timeout_seconds)
    if callback.error:
        if callback.error == "access_denied":
            raise DesktopOAuthError("oauth_cancelled")
        raise DesktopOAuthError("oauth_provider_failed")
    if not callback.code:
        raise DesktopOAuthError("oauth_callback_invalid")
    if cancel_event.is_set():
        raise DesktopOAuthError("oauth_cancelled")

    return exchange_pkce_code(
        supabase_url,
        publishable_key,
        callback.code,
        verifier,
        user_agent=user_agent,
    )


__all__ = [
    "DesktopOAuthError",
    "LoopbackOAuthReceiver",
    "OAuthCallback",
    "build_authorize_url",
    "create_pkce_pair",
    "exchange_pkce_code",
    "run_social_oauth",
]
