from __future__ import annotations

from pathlib import Path

import pytest

from app.browser_instance import (
    MANAGED_MAKRO_CDP_PORT_ENV,
    browser_instance_namespace,
    managed_makro_cdp_port,
)


def test_browser_namespace_is_stable_and_runtime_root_specific(tmp_path: Path) -> None:
    stable = tmp_path / "stable"
    dev = tmp_path / "dev"

    assert browser_instance_namespace(stable) == browser_instance_namespace(stable)
    assert browser_instance_namespace(stable) != browser_instance_namespace(dev)

    stable_port = managed_makro_cdp_port(stable, environ={})
    assert 12000 <= stable_port <= 31999
    assert stable_port != 9222
    assert stable_port == managed_makro_cdp_port(stable, environ={})


def test_explicit_managed_port_override_is_validated(tmp_path: Path) -> None:
    assert managed_makro_cdp_port(
        tmp_path,
        environ={MANAGED_MAKRO_CDP_PORT_ENV: "24567"},
    ) == 24567

    with pytest.raises(ValueError, match="integer TCP port"):
        managed_makro_cdp_port(
            tmp_path,
            environ={MANAGED_MAKRO_CDP_PORT_ENV: "not-a-port"},
        )
    with pytest.raises(ValueError, match="1..65535"):
        managed_makro_cdp_port(
            tmp_path,
            environ={MANAGED_MAKRO_CDP_PORT_ENV: "70000"},
        )


def test_gui_browser_owner_and_updater_use_instance_port() -> None:
    root = Path(__file__).resolve().parents[1]
    browser_owner = (root / "gui" / "channel_account_browser.py").read_text(encoding="utf-8")
    updater = (root / "gui" / "app_updater.py").read_text(encoding="utf-8")

    assert "managed_makro_cdp_port(project_root)" in browser_owner
    assert "super().__init__(window, port=managed_port)" in browser_owner
    assert "single_port.setValue(int(port))" in browser_owner
    assert "batch_port.setValue(int(port))" in browser_owner

    assert "DEFAULT_CDP_PORT" not in updater
    assert "port=browser_port" in updater
