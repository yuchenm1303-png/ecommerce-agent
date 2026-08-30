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

comment on function public.activate_listing_portal_device_v1(uuid, text, text, smallint, text, text, boolean) is
  'Atomically validates/activates one licensed device. The user access row serializes concurrent slot allocation so max_devices cannot be exceeded by racing activations.';

revoke all on function public.activate_listing_portal_device_v1(uuid, text, text, smallint, text, text, boolean)
  from public, anon, authenticated;
grant execute on function public.activate_listing_portal_device_v1(uuid, text, text, smallint, text, text, boolean)
  to service_role;
