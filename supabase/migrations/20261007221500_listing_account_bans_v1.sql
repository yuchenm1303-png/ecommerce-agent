-- Explicit account bans are separate from ordinary license enablement/expiry.
alter table public.download_portal_users
  add column if not exists banned_at timestamptz,
  add column if not exists ban_reason text,
  add column if not exists banned_by uuid references auth.users(id) on delete set null;

alter table public.download_portal_users
  drop constraint if exists download_portal_users_ban_reason_length;
alter table public.download_portal_users
  add constraint download_portal_users_ban_reason_length
    check (ban_reason is null or char_length(ban_reason) <= 500);

create table if not exists public.listing_account_ban_events (
  id bigint generated always as identity primary key,
  target_user_id uuid not null references auth.users(id) on delete cascade,
  actor_user_id uuid references auth.users(id) on delete set null,
  action text not null check (action in ('ban', 'unban')),
  reason text not null default '',
  created_at timestamptz not null default now(),
  constraint listing_account_ban_events_reason_length check (char_length(reason) <= 500)
);
create index if not exists listing_account_ban_events_target_time_idx
  on public.listing_account_ban_events (target_user_id, created_at desc);

alter table public.listing_account_ban_events enable row level security;
revoke all on public.listing_account_ban_events from anon, authenticated;
grant select, insert on public.listing_account_ban_events to service_role;

-- Only the verified service-role Edge route may call this function. Authorization is
-- checked again inside the transaction; row locks serialize bans vs license checks.
create or replace function public.set_listing_account_ban_v1(
  p_actor uuid,
  p_target uuid,
  p_action text,
  p_reason text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor public.download_portal_users%rowtype;
  v_target public.download_portal_users%rowtype;
  v_reason text := btrim(coalesce(p_reason, ''));
  v_now timestamptz := now();
begin
  if p_actor is null or p_target is null or p_action not in ('ban', 'unban') then
    return jsonb_build_object('error', 'invalid_request');
  end if;
  if p_actor = p_target then
    return jsonb_build_object('error', 'cannot_ban_self');
  end if;
  if char_length(v_reason) > 500 or (p_action = 'ban' and v_reason = '') then
    return jsonb_build_object('error', 'invalid_reason');
  end if;

  select * into v_actor
  from public.download_portal_users
  where user_id = p_actor
  for update;
  if not found or not coalesce(v_actor.enabled, false)
     or not coalesce(v_actor.is_admin, false) or v_actor.banned_at is not null then
    return jsonb_build_object('error', 'not_authorized');
  end if;

  select * into v_target
  from public.download_portal_users
  where user_id = p_target
  for update;
  if not found then
    return jsonb_build_object('error', 'account_not_found');
  end if;
  if p_action = 'ban' and coalesce(v_target.is_admin, false) then
    return jsonb_build_object('error', 'cannot_ban_admin');
  end if;

  if p_action = 'ban' and v_target.banned_at is not null
     or p_action = 'unban' and v_target.banned_at is null then
    return jsonb_build_object(
      'ok', true,
      'unchanged', true,
      'user_id', p_target,
      'banned_at', v_target.banned_at,
      'ban_reason', coalesce(v_target.ban_reason, '')
    );
  end if;

  if p_action = 'ban' then
    update public.download_portal_users
      set banned_at = v_now, ban_reason = v_reason, banned_by = p_actor
      where user_id = p_target;
    -- Existing telemetry tokens and device activations cannot survive a ban.
    update public.download_portal_devices
      set enabled = false, telemetry_token_hash = null, updated_at = v_now
      where user_id = p_target;
  else
    update public.download_portal_users
      set banned_at = null, ban_reason = null, banned_by = null
      where user_id = p_target;
  end if;

  insert into public.listing_account_ban_events
    (target_user_id, actor_user_id, action, reason, created_at)
  values (p_target, p_actor, p_action, v_reason, v_now);

  return jsonb_build_object(
    'ok', true,
    'unchanged', false,
    'user_id', p_target,
    'banned_at', case when p_action = 'ban' then to_jsonb(v_now) else 'null'::jsonb end,
    'ban_reason', case when p_action = 'ban' then v_reason else '' end
  );
end;
$$;

revoke all on function public.set_listing_account_ban_v1(uuid,uuid,text,text)
  from public, anon, authenticated;
grant execute on function public.set_listing_account_ban_v1(uuid,uuid,text,text)
  to service_role;

-- Replace the existing serialised license gate so a banned user never acquires
-- a new telemetry token, even if a concurrent activation request is racing.
create or replace function public.activate_listing_portal_device_v1(
  p_user_id uuid,
  p_device_id text,
  p_device_name text,
  p_fingerprint_version smallint,
  p_app_version text,
  p_telemetry_token_hash text,
  p_activate boolean
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_access public.download_portal_users%rowtype;
  v_device public.download_portal_devices%rowtype;
  v_now timestamptz := now();
  v_max_devices integer;
  v_active_devices integer;
begin
  if p_user_id is null or p_device_id is null or btrim(p_device_id) = '' then
    return jsonb_build_object('error', 'invalid_device');
  end if;

  -- The access row is the per-user serialization point. Concurrent activation
  -- requests for the same account cannot both observe the same free device slot.
  select *
    into v_access
    from public.download_portal_users
   where user_id = p_user_id
   for update;

  if found and v_access.banned_at is not null then
    return jsonb_build_object('error', 'account_banned');
  end if;
  if not found or not coalesce(v_access.enabled, false) then
    return jsonb_build_object('error', 'not_authorized');
  end if;
  if v_access.expires_at is not null and v_access.expires_at <= v_now then
    return jsonb_build_object('error', 'access_expired');
  end if;

  v_max_devices := greatest(1, coalesce(v_access.max_devices, 2));

  select *
    into v_device
    from public.download_portal_devices
   where user_id = p_user_id
     and device_id = p_device_id
   for update;

  if found and v_device.revoked_at is not null then
    return jsonb_build_object('error', 'device_revoked');
  end if;

  if not found or not coalesce(v_device.enabled, false) then
    if not p_activate then
      return jsonb_build_object('error', 'device_not_activated');
    end if;

    select count(*)::integer
      into v_active_devices
      from public.download_portal_devices
     where user_id = p_user_id
       and enabled = true
       and revoked_at is null;

    if v_active_devices >= v_max_devices then
      return jsonb_build_object(
        'error', 'device_limit_reached',
        'max_devices', v_max_devices,
        'active_devices', v_active_devices
      );
    end if;

    insert into public.download_portal_devices (
      user_id,
      device_id,
      device_name,
      fingerprint_version,
      enabled,
      app_version,
      telemetry_token_hash,
      first_seen_at,
      last_seen_at,
      created_at,
      updated_at
    ) values (
      p_user_id,
      p_device_id,
      left(coalesce(p_device_name, ''), 160),
      greatest(1, coalesce(p_fingerprint_version, 1)),
      true,
      left(coalesce(p_app_version, ''), 64),
      p_telemetry_token_hash,
      v_now,
      v_now,
      v_now,
      v_now
    )
    on conflict (user_id, device_id) do update set
      enabled = true,
      device_name = excluded.device_name,
      fingerprint_version = excluded.fingerprint_version,
      app_version = excluded.app_version,
      telemetry_token_hash = excluded.telemetry_token_hash,
      last_seen_at = excluded.last_seen_at,
      updated_at = excluded.updated_at
    where public.download_portal_devices.revoked_at is null;
  else
    update public.download_portal_devices
       set device_name = left(coalesce(p_device_name, ''), 160),
           fingerprint_version = greatest(1, coalesce(p_fingerprint_version, 1)),
           app_version = left(coalesce(p_app_version, ''), 64),
           telemetry_token_hash = p_telemetry_token_hash,
           last_seen_at = v_now,
           updated_at = v_now
     where user_id = p_user_id
       and device_id = p_device_id
       and revoked_at is null;
  end if;

  select count(*)::integer
    into v_active_devices
    from public.download_portal_devices
   where user_id = p_user_id
     and enabled = true
     and revoked_at is null;

  return jsonb_build_object(
    'authorized', true,
    'display_name', coalesce(v_access.display_name, ''),
    'expires_at', v_access.expires_at,
    'max_devices', v_max_devices,
    'active_devices', v_active_devices,
    'grace_period_hours', greatest(0, coalesce(v_access.grace_period_hours, 72)),
    'device_id', p_device_id,
    'validated_at', v_now
  );
end;
$$;

