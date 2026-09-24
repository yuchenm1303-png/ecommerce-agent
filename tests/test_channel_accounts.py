from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.browser_instance import managed_makro_cdp_port
from app.channel_accounts import ChannelAccountStore


ROOT = Path(__file__).resolve().parents[1]
RUN_LOCAL = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
ACCOUNT_BROWSER = (ROOT / "gui" / "channel_account_browser.py").read_text(encoding="utf-8")


def test_channel_account_metadata_is_scoped_and_contains_no_marketplace_secret(tmp_path: Path) -> None:
    store = ChannelAccountStore(tmp_path, scope_id="user-a")
    active = store.active_account("makro")

    assert active.channel == "makro"
    assert store.profile_dir(active).is_relative_to(tmp_path / "browser_profiles")

    payload = store.state_path.read_text(encoding="utf-8")
    assert "user-a" not in payload
    assert "password" not in payload.casefold()
    assert "cookie" not in payload.casefold()
    assert "access_token" not in payload.casefold()


def test_existing_legacy_makro_profile_can_only_be_claimed_by_one_scope(tmp_path: Path) -> None:
    legacy = tmp_path / "browser_profiles" / "makro-edge"
    legacy.mkdir(parents=True)

    first = ChannelAccountStore(tmp_path, scope_id="user-a")
    first_account = first.active_account("makro")
    second = ChannelAccountStore(tmp_path, scope_id="user-b")
    second_account = second.active_account("makro")

    assert first.profile_dir(first_account) == legacy.resolve()
    assert second.profile_dir(second_account) != legacy.resolve()
    assert first.runtime_identity(first_account) != second.runtime_identity(second_account)


def test_store_supports_multiple_accounts_without_sharing_profiles(tmp_path: Path) -> None:
    store = ChannelAccountStore(tmp_path, scope_id="user-a")
    original = store.active_account("makro")
    second = store.create_account("makro", label="Makro 店铺 B")
    selected = store.set_active("makro", second.account_id)

    assert selected == second
    assert store.active_account("makro") == second
    assert store.profile_dir(original) != store.profile_dir(second)
    assert len(store.list_accounts("makro")) == 2


def test_makro_accounts_receive_distinct_persisted_browser_ports(tmp_path: Path) -> None:
    store = ChannelAccountStore(tmp_path, scope_id="user-a")
    original = store.active_account("makro")
    second = store.create_account("makro", label="Makro 店铺 B")
    third = store.create_account("makro", label="Makro 店铺 C")

    original_port = store.cdp_port(original)
    second_port = store.cdp_port(second)
    third_port = store.cdp_port(third)

    assert original_port == managed_makro_cdp_port(tmp_path)
    assert len({original_port, second_port, third_port}) == 3

    reloaded = ChannelAccountStore(tmp_path, scope_id="user-a")
    accounts = {account.label: account for account in reloaded.list_accounts("makro")}
    assert reloaded.cdp_port(accounts[second.label]) == second_port
    assert reloaded.cdp_port(accounts[third.label]) == third_port

    payload = json.loads(store.state_path.read_text(encoding="utf-8"))
    ports = [int(row["cdp_port"]) for row in payload["accounts"] if row["channel"] == "makro"]
    assert len(ports) == len(set(ports))


def test_legacy_state_without_cdp_ports_is_migrated_lazily(tmp_path: Path) -> None:
    store = ChannelAccountStore(tmp_path, scope_id="user-a")
    original = store.active_account("makro")
    second = store.create_account("makro", label="Makro 店铺 B")

    payload = json.loads(store.state_path.read_text(encoding="utf-8"))
    for row in payload["accounts"]:
        row.pop("cdp_port", None)
    store.state_path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = ChannelAccountStore(tmp_path, scope_id="user-a")
    accounts = {account.account_id: account for account in reloaded.list_accounts("makro")}
    original_port = reloaded.cdp_port(accounts[original.account_id])
    second_port = reloaded.cdp_port(accounts[second.account_id])

    assert original_port != second_port
    assert original_port == managed_makro_cdp_port(tmp_path)


def test_unknown_account_cannot_be_selected(tmp_path: Path) -> None:
    store = ChannelAccountStore(tmp_path, scope_id="user-a")
    store.active_account("makro")

    with pytest.raises(KeyError):
        store.set_active("makro", "missing")


def test_formal_gui_routes_managed_makro_browser_through_channel_account_owner() -> None:
    assert "from gui.channel_account_browser import install_managed_makro_browser" in RUN_LOCAL
    assert "install_managed_makro_browser(window)" in RUN_LOCAL
    assert "class AccountBoundMakroBrowser(ManagedMakroBrowser)" in ACCOUNT_BROWSER
    assert "ChannelAccountStore(" in ACCOUNT_BROWSER
    assert "close_managed_browser(" in ACCOUNT_BROWSER
    assert "def channel_account_change_blocked" in ACCOUNT_BROWSER
    assert "独立 Batch 不受此限制" in ACCOUNT_BROWSER


def test_channel_account_sources_compile() -> None:
    account_store = (ROOT / "app" / "channel_accounts.py").read_text(encoding="utf-8")
    browser_instance = (ROOT / "app" / "browser_instance.py").read_text(encoding="utf-8")
    compile(account_store, str(ROOT / "app" / "channel_accounts.py"), "exec")
    compile(browser_instance, str(ROOT / "app" / "browser_instance.py"), "exec")
    compile(ACCOUNT_BROWSER, str(ROOT / "gui" / "channel_account_browser.py"), "exec")
