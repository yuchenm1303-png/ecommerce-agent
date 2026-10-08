from __future__ import annotations

import __future__
import ast
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


SOURCE = (Path(__file__).resolve().parents[1] / "gui" / "app_access.py").read_text(encoding="utf-8")
MODULE = ast.parse(SOURCE)


class AccessError(RuntimeError):
    def __init__(self, code: str, *, status: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class AccessNetworkError(AccessError):
    pass


def _isolated_function(name: str, context: dict):
    """Execute one production function without importing Qt on headless CI."""
    func = next(node for node in MODULE.body if isinstance(node, ast.FunctionDef) and node.name == name)
    isolated = ast.Module(body=[func], type_ignores=[])
    ast.fix_missing_locations(isolated)
    namespace = dict(context)
    exec(
        compile(isolated, filename="gui/app_access.py", mode="exec", flags=__future__.annotations.compiler_flag),
        namespace,
    )
    return namespace[name]


def _sign_in_harness(device_result: str = "ok"):
    actions = []
    saved = []

    def license_check(token, *, action, device_id, device_name):
        actions.append(action)
        if action == "validate" and device_result != "ok":
            raise AccessError(device_result, status=403)
        return {"authorized": True}

    session = SimpleNamespace(enforced=True, refresh_token="refresh")
    func = _isolated_function(
        "_complete_sign_in",
        {
            "AccessError": AccessError,
            "device_identity": lambda: ("device-id", "this-pc"),
            "_license_check": license_check,
            "_session_from_auth": lambda *args, **kwargs: session,
            "_save_state": saved.append,
        },
    )
    auth = {"access_token": "access", "refresh_token": "refresh", "user": {"email": "test@example.org"}}
    return func, auth, session, actions, saved


def test_existing_device_only_validates_and_saves_session():
    finish, auth, session, actions, saved = _sign_in_harness()
    assert finish(auth) is session
    assert actions == ["validate"]
    assert saved == [session]


def test_new_device_must_validate_then_explicitly_activate():
    finish, auth, session, actions, saved = _sign_in_harness("device_not_activated")
    with pytest.raises(AccessError, match="device_not_activated"):
        finish(auth)
    assert actions == ["validate"]
    assert saved == []
    assert finish(auth, action="activate") is session
    assert actions == ["validate", "activate"]
    assert saved == [session]


def test_revoked_device_cannot_enter_or_implicitly_reactivate():
    finish, auth, _session, actions, saved = _sign_in_harness("device_revoked")
    with pytest.raises(AccessError, match="device_revoked"):
        finish(auth)
    assert actions == ["validate"]
    assert saved == []


def test_oauth_worker_must_not_persist_before_user_accepts():
    finish, auth, session, actions, saved = _sign_in_harness()
    assert finish(auth, persist=False) is session
    assert actions == ["validate"]
    assert saved == []


def test_missing_auth_credentials_never_touch_device():
    finish, auth, _session, actions, saved = _sign_in_harness()
    auth.pop("refresh_token")
    with pytest.raises(AccessError, match="invalid_auth"):
        finish(auth)
    assert actions == []
    assert saved == []


@pytest.mark.parametrize("status", [429, 500, 503])
def test_temporary_http_errors_are_network_failures(status):
    request = _isolated_function(
        "_request_json",
        {
            "AccessError": AccessError,
            "AccessNetworkError": AccessNetworkError,
            "_SUPABASE_PUBLISHABLE_KEY": "public-test-only",
            "_HTTP_TIMEOUT_SECONDS": 1,
            "_installed_version": lambda: "test",
            "json": json,
            "urllib": __import__("urllib"),
        },
    )
    error = urllib.error.HTTPError(
        "https://example.invalid", status, "temporary failure", {},
        io.BytesIO(b'{"error":"device_check_failed"}'),
    )
    with patch.object(urllib.request, "urlopen", side_effect=error):
        with pytest.raises(AccessNetworkError) as raised:
            request("https://example.invalid", {"ok": True})
    assert raised.value.status == status


def test_explicit_access_denial_is_not_misclassified_as_network_error():
    request = _isolated_function(
        "_request_json",
        {
            "AccessError": AccessError,
            "AccessNetworkError": AccessNetworkError,
            "_SUPABASE_PUBLISHABLE_KEY": "public-test-only",
            "_HTTP_TIMEOUT_SECONDS": 1,
            "_installed_version": lambda: "test",
            "json": json,
            "urllib": __import__("urllib"),
        },
    )
    error = urllib.error.HTTPError(
        "https://example.invalid", 403, "forbidden", {},
        io.BytesIO(b'{"error":"device_revoked"}'),
    )
    with patch.object(urllib.request, "urlopen", side_effect=error):
        with pytest.raises(AccessError) as raised:
            request("https://example.invalid", {"ok": True})
    assert type(raised.value) is AccessError
    assert raised.value.code == "device_revoked"


def test_login_window_has_explicit_confirmation_only_for_new_devices():
    login = next(node for node in MODULE.body if isinstance(node, ast.ClassDef) and node.name == "_LoginDialog")
    methods = {node.name: ast.get_source_segment(SOURCE, node) for node in login.body if isinstance(node, ast.FunctionDef)}
    assert 'action="validate"' in methods["_login"]
    assert 'exc.code != "device_not_activated"' in methods["_login"]
    assert 'action="activate"' in methods["_activate_pending_device"]
    assert "确认激活此设备" in methods["_prepare_activation"]
    assert 'action="validate", persist=False' in methods["_start_oauth"]
    assert "_save_state(session)" in methods["_poll_oauth_result"]
    assert "_reset_pending_activation()" in methods["_start_oauth"]


def test_syntax_compiles_for_package():
    compile(SOURCE, "gui/app_access.py", "exec")
