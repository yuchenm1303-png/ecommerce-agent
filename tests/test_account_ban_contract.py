"""Regression checks for the account moderation security boundaries.

These are source-contract tests; live DB / Edge integration still needs a
staging project before the feature is deployed.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_moderation_rpc_requires_server_role_and_audits_mutations() -> None:
    migration = source("supabase/migrations/20261007221500_listing_account_bans_v1.sql")
    assert "listing_account_ban_events" in migration
    assert "cannot_ban_self" in migration
    assert "cannot_ban_admin" in migration
    assert "char_length(v_reason) > 500" in migration
    assert "revoke all on function public.set_listing_account_ban_v1" in migration
    assert "to service_role;" in migration
    assert "telemetry_token_hash = null" in migration
    assert "if found and v_access.banned_at is not null then" in migration
    assert "'account_banned'" in migration


def test_all_live_application_endpoints_deny_banned_users() -> None:
    for path in (
        "supabase/functions/portal-license/index.ts",
        "supabase/functions/portal-telemetry/index.ts",
        "supabase/functions/portal-download/index.ts",
    ):
        code = source(path)
        assert "banned_at" in code, path
        assert "account_banned" in code, path


def test_owner_actions_are_authenticated_and_server_side() -> None:
    code = source("supabase/functions/portal-usage-admin/index.ts")
    assert "userClient.auth.getUser()" in code
    assert "access.banned_at" in code
    assert "access?.is_admin" in code
    assert "set_listing_account_ban_v1" in code
    assert 'ban_duration: "876000h"' in code
    assert 'ban_duration: "none"' in code
    assert "account_statuses" in code


def test_desktop_never_uses_offline_grace_to_bypass_ban() -> None:
    code = source("gui/app_access.py")
    assert '"account_banned"' in code
    assert '"user_banned"' in code
    assert "_REVALIDATE_INTERVAL_MS = 3 * 60 * 1000" in code
    assert "if session.grace_until > time.time()" not in code
    assert "if self.session.grace_until > time.time()" not in code
