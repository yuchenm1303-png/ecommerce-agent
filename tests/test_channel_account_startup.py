"""Regression: the Makro account manager must build its marker before Qt base init.

Run without PySide6 so this pre-super constructor bug is caught in headless CI.
"""
from __future__ import annotations

import __future__
import ast
import threading
from pathlib import Path
from types import SimpleNamespace

SOURCE_PATH = Path(__file__).resolve().parents[1] / "gui" / "channel_account_browser.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")


class FakeManagedBrowser:
    def __init__(self, window, *, port: int):
        # Emulate precisely when the real parent initializes this attribute.
        assert not hasattr(self, "project_root"), "project_root must be set by parent"
        assert self._runtime_marker.is_absolute(), "runtime marker was not resolved"
        self.project_root = Path(window.project_root).resolve()
        self.window = window
        self.port = port


class FakeChannelAccountStore:
    def __init__(self, project_root, *, scope_id):
        self.root = Path(project_root)
        self.scope_id = scope_id
        self.scope_token = "test-scope"

    def active_account(self, channel):
        assert channel == "makro"
        return SimpleNamespace(account_id="account-1", channel=channel)

    def cdp_port(self, account):
        return 9241

    def profile_dir(self, account):
        return self.root / "browser_profiles" / account.account_id

    def runtime_identity(self, account):
        return "identity-for-" + account.account_id


def _load_real_init_without_qt():
    module = ast.parse(SOURCE, filename=str(SOURCE_PATH))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "AccountBoundMakroBrowser")
    names = {"__init__", "_runtime_marker_for", "_sync_managed_port_controls", "_current_scope_id"}
    subset = [
        n for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names
    ]
    assert len(subset) == 4
    fake_cls = ast.ClassDef(
        name="AccountBoundMakroBrowser",
        bases=[ast.Name(id="ManagedMakroBrowser", ctx=ast.Load())],
        keywords=[],
        body=[ast.Assign(targets=[ast.Name(id="_CHANNEL", ctx=ast.Store())], value=ast.Constant(value="makro")), *subset],
        decorator_list=[],
    )
    isolated = ast.fix_missing_locations(ast.Module(body=[fake_cls], type_ignores=[]))
    ns = {
        "ManagedMakroBrowser": FakeManagedBrowser,
        "ChannelAccountStore": FakeChannelAccountStore,
        "Path": Path,
        "threading": threading,
    }
    exec(
        compile(isolated, str(SOURCE_PATH), "exec", flags=__future__.annotations.compiler_flag),
        ns,
    )
    return ns["AccountBoundMakroBrowser"]


def test_account_bound_browser_starts_without_project_root_attribute_error(tmp_path):
    browser_cls = _load_real_init_without_qt()
    window = SimpleNamespace(project_root=tmp_path)
    browser = browser_cls(window)
    expected = (
        tmp_path.resolve() / "channel_accounts" / "managed_makro_browsers"
        / "test-scope" / "account-1.json"
    )
    assert browser._runtime_marker == expected
    assert browser.project_root == tmp_path.resolve()
    assert browser.profile_dir == tmp_path / "browser_profiles" / "account-1"
    assert browser.port == 9241


def test_account_marker_after_base_init_uses_managed_root(tmp_path):
    browser_cls = _load_real_init_without_qt()
    browser = browser_cls(SimpleNamespace(project_root=tmp_path))
    next_account = SimpleNamespace(account_id="account-2")
    assert browser._runtime_marker_for(next_account) == (
        tmp_path.resolve() / "channel_accounts" / "managed_makro_browsers"
        / "test-scope" / "account-2.json"
    )


def test_account_scope_is_preserved_through_startup(tmp_path):
    browser_cls = _load_real_init_without_qt()
    session = SimpleNamespace(enforced=True, user_id="account-user-42")
    window = SimpleNamespace(
        project_root=tmp_path,
        _application_access=SimpleNamespace(session=session),
    )
    browser = browser_cls(window)
    assert browser.channel_accounts.scope_id == "account-user-42"


def test_source_parses_for_frozen_windows_entrypoint():
    compile(SOURCE, str(SOURCE_PATH), "exec")
