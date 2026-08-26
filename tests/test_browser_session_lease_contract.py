from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.browser_session as bs


class _FakeLease:
    def __init__(self) -> None:
        self.release_calls = 0

    def release(self) -> None:
        self.release_calls += 1


def _isolate_lease_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bs, "_cdp_lock_root", lambda: tmp_path)
    bs._CDP_SESSION_LOCAL_LOCKS.clear()


def test_same_owner_can_reenter_for_synchronous_child_without_deadlock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_lease_files(monkeypatch, tmp_path)
    port = 39221
    env_key = bs._cdp_session_env_key(port)
    monkeypatch.delenv(env_key, raising=False)

    root = bs.acquire_cdp_session_lease(port)
    try:
        assert root.inherited is False
        assert os.environ[env_key] == root.token

        child = bs.acquire_cdp_session_lease(port)
        try:
            assert child.inherited is True
            assert child.token == root.token
            assert bs._read_cdp_session_owner(port)["token"] == root.token
        finally:
            child.release()

        # Releasing an inherited child must never release the root owner's lock.
        assert bs._read_cdp_session_owner(port)["token"] == root.token
        assert os.environ[env_key] == root.token
    finally:
        root.release()

    assert not bs._cdp_session_owner_path(port).exists()
    assert env_key not in os.environ


def test_stale_inherited_token_cannot_bypass_root_ownership(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_lease_files(monkeypatch, tmp_path)
    port = 39222
    env_key = bs._cdp_session_env_key(port)
    monkeypatch.setenv(env_key, "stale-child-token")
    bs._cdp_session_owner_path(port).write_text(
        '{"schema_version":1,"port":39222,"token":"different-live-owner","owner_pid":1}',
        encoding="utf-8",
    )

    lease = bs.acquire_cdp_session_lease(port)
    try:
        assert lease.inherited is False
        assert lease.token != "stale-child-token"
        assert os.environ[env_key] == lease.token
    finally:
        lease.release()


def test_edge_harness_holds_session_lease_until_detach(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lease = _FakeLease()
    monkeypatch.setattr(bs, "acquire_cdp_session_lease", lambda _port: lease)
    monkeypatch.setattr(bs, "is_cdp_ready", lambda _port, **_kw: True)

    def fake_connect(self: bs.EdgeHarness) -> None:
        self.browser = SimpleNamespace()
        self.context = SimpleNamespace()
        self.page = SimpleNamespace()

    monkeypatch.setattr(bs.EdgeHarness, "_connect", fake_connect)

    harness = bs.EdgeHarness(
        SimpleNamespace(),
        profile_dir=tmp_path / "makro-edge",
        port=39223,
    )
    assert lease.release_calls == 0

    harness.detach()
    assert lease.release_calls == 1

    # detach is intentionally idempotent; callers may clean up in finally blocks.
    harness.detach()
    assert lease.release_calls == 1


def test_edge_harness_releases_session_lease_when_attach_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lease = _FakeLease()
    monkeypatch.setattr(bs, "acquire_cdp_session_lease", lambda _port: lease)
    monkeypatch.setattr(bs, "is_cdp_ready", lambda _port, **_kw: True)

    def broken_connect(_self: bs.EdgeHarness) -> None:
        raise RuntimeError("simulated CDP attach failure")

    monkeypatch.setattr(bs.EdgeHarness, "_connect", broken_connect)

    with pytest.raises(RuntimeError, match="simulated CDP attach failure"):
        bs.EdgeHarness(
            SimpleNamespace(),
            profile_dir=tmp_path / "makro-edge",
            port=39224,
        )

    assert lease.release_calls == 1


def test_session_ownership_is_port_scoped() -> None:
    assert bs._cdp_session_lock_path(9222) != bs._cdp_session_lock_path(9333)
    assert bs._cdp_session_env_key(9222) != bs._cdp_session_env_key(9333)
