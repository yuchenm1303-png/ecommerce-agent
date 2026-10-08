from __future__ import annotations

import base64
import hashlib
import threading
import urllib.parse
import urllib.request

import pytest

from app import desktop_oauth


def test_create_pkce_pair_is_s256_and_urlsafe() -> None:
    verifier, challenge = desktop_oauth.create_pkce_pair()

    assert 43 <= len(verifier) <= 128
    assert "=" not in verifier
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    assert challenge == expected


def test_build_authorize_url_matches_supabase_social_pkce_contract() -> None:
    url = desktop_oauth.build_authorize_url(
        "https://project.supabase.co",
        "github",
        "http://127.0.0.1:47843/oauth/callback",
        "challenge-value",
    )

    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.path == "/auth/v1/authorize"
    assert query == {
        "provider": ["github"],
        "redirect_to": ["http://127.0.0.1:47843/oauth/callback"],
        "code_challenge": ["challenge-value"],
        "code_challenge_method": ["s256"],
    }


def test_build_authorize_url_rejects_unknown_provider() -> None:
    with pytest.raises(desktop_oauth.DesktopOAuthError) as caught:
        desktop_oauth.build_authorize_url(
            "https://project.supabase.co",
            "unknown",
            "http://127.0.0.1:47843/oauth/callback",
            "challenge",
        )
    assert caught.value.code == "oauth_provider_invalid"


def test_loopback_receiver_accepts_expected_callback() -> None:
    receiver = desktop_oauth.LoopbackOAuthReceiver(port=0)
    cancel = threading.Event()
    outcome: dict[str, object] = {}

    def wait() -> None:
        outcome["callback"] = receiver.wait(cancel, timeout_seconds=3)

    thread = threading.Thread(target=wait)
    thread.start()

    with urllib.request.urlopen(receiver.redirect_uri + "?code=auth-code", timeout=2) as response:
        assert response.status == 200
        body = response.read().decode("utf-8")
        assert "登录已完成" in body

    thread.join(timeout=3)
    assert not thread.is_alive()
    callback = outcome["callback"]
    assert isinstance(callback, desktop_oauth.OAuthCallback)
    assert callback.code == "auth-code"


def test_run_social_oauth_exchanges_callback_code(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_exchange(
        supabase_url: str,
        publishable_key: str,
        code: str,
        verifier: str,
        *,
        user_agent: str,
        timeout: int = 12,
    ) -> dict[str, str]:
        captured.update(
            {
                "supabase_url": supabase_url,
                "publishable_key": publishable_key,
                "code": code,
                "verifier": verifier,
                "user_agent": user_agent,
            }
        )
        return {"access_token": "access", "refresh_token": "refresh"}

    monkeypatch.setattr(desktop_oauth, "exchange_pkce_code", fake_exchange)

    def fake_browser(url: str) -> bool:
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        callback_url = params["redirect_to"][0] + "?code=provider-code"

        def visit() -> None:
            with urllib.request.urlopen(callback_url, timeout=2) as response:
                response.read()

        threading.Thread(target=visit, daemon=True).start()
        return True

    result = desktop_oauth.run_social_oauth(
        "google",
        supabase_url="https://project.supabase.co",
        publishable_key="public-key",
        user_agent="ListingStudio/test",
        cancel_event=threading.Event(),
        open_browser=fake_browser,
        callback_port=0,
        timeout_seconds=3,
    )

    assert result["access_token"] == "access"
    assert captured["code"] == "provider-code"
    assert captured["publishable_key"] == "public-key"
    assert captured["verifier"]


def test_desktop_login_dialog_keeps_existing_license_gate() -> None:
    from pathlib import Path

    source = Path("gui/app_access.py").read_text(encoding="utf-8")
    assert "run_social_oauth(" in source
    assert 'action="activate"' in source
    assert 'action="deactivate"' in source
    assert "_license_check(" in source
    assert "_save_state(session)" in source
    assert "使用 Google 继续" in source
    assert "使用 GitHub 继续" in source
