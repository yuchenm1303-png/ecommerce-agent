create or replace function public.get_listing_usage_admin_snapshot(p_caller uuid)
returns jsonb
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  payload jsonb;
begin
  if p_caller is null or not exists (
    select 1
    from public.download_portal_users
    where user_id = p_caller and is_admin = true and enabled = true
  ) then
    raise exception 'not_authorized' using errcode = '42501';
  end if;

  with hours as (
    select generate_series(
      date_trunc('hour', now()) - interval '23 hours',
      date_trunc('hour', now()),
      interval '1 hour'
    ) as bucket_start
  ),
  session_stats as (
    select
      user_id,
      count(*)::bigint as launch_count,
      min(started_at) as first_used_at,
      max(last_seen_at) as session_last_seen_at
    from public.listing_usage_sessions
    group by user_id
  ),
  event_stats as (
    select
      user_id,
      count(*) filter (where event_type='listing_prepare' and outcome='started')::bigint as listing_prepare_started,
      count(*) filter (where event_type='listing_prepare' and outcome='completed')::bigint as listing_prepare_completed,
      count(*) filter (where event_type='listing_prepare' and outcome='failed')::bigint as listing_prepare_failed,
      count(*) filter (where event_type='listing_execute' and outcome='started')::bigint as listing_execute_started,
      count(*) filter (where event_type='listing_execute' and outcome='completed')::bigint as listing_execute_completed,
      count(*) filter (where event_type='listing_execute' and outcome='failed')::bigint as listing_execute_failed,
      count(*) filter (where event_type='batch_prepare' and outcome='started')::bigint as batch_prepare_started,
      count(*) filter (where event_type='batch_prepare' and outcome='completed')::bigint as batch_prepare_completed,
      count(*) filter (where event_type='batch_prepare' and outcome='failed')::bigint as batch_prepare_failed,
      count(*) filter (where event_type='batch_execute' and outcome='started')::bigint as batch_execute_started,
      count(*) filter (where event_type='batch_execute' and outcome='completed')::bigint as batch_execute_completed,
      count(*) filter (where event_type='batch_execute' and outcome='failed')::bigint as batch_execute_failed
    from public.listing_usage_events
    group by user_id
  ),
  device_stats as (
    select
      user_id,
      count(*) filter (where enabled=true and revoked_at is null)::bigint as active_devices,
      max(last_seen_at) as device_last_seen_at
    from public.download_portal_devices
    group by user_id
  ),
  account_hours as (
    select p.user_id, h.bucket_start
    from public.download_portal_users p
    cross join hours h
  ),
  hourly_activity as (
    select
      ah.user_id,
      ah.bucket_start,
      exists (
        select 1
        from public.listing_usage_sessions s
        where s.user_id = ah.user_id
          and s.started_at < ah.bucket_start + interval '1 hour'
          and coalesce(s.ended_at, s.last_seen_at) >= ah.bucket_start
      ) as active,
      (
        select count(*)::bigint
        from public.listing_usage_sessions s
        where s.user_id = ah.user_id
          and s.started_at >= ah.bucket_start
          and s.started_at < ah.bucket_start + interval '1 hour'
      ) as launches,
      (
        select count(*)::bigint
        from public.listing_usage_events e
        where e.user_id = ah.user_id
          and e.occurred_at >= ah.bucket_start
          and e.occurred_at < ah.bucket_start + interval '1 hour'
          and e.outcome = 'completed'
          and e.event_type in ('listing_execute','batch_execute')
      ) as completed,
      (
        select count(*)::bigint
        from public.listing_usage_events e
        where e.user_id = ah.user_id
          and e.occurred_at >= ah.bucket_start
          and e.occurred_at < ah.bucket_start + interval '1 hour'
          and e.outcome = 'failed'
      ) as failed
    from account_hours ah
  ),
  activity_stats as (
    select
      user_id,
      jsonb_agg(
        jsonb_build_object(
          'bucket_start', bucket_start,
          'active', active,
          'launches', launches,
          'completed', completed,
          'failed', failed
        )
        order by bucket_start
      ) as activity_24h
    from hourly_activity
    group by user_id
  ),
  rows as (
    select
      p.user_id,
      u.email,
      coalesce(nullif(p.display_name,''), u.email, '') as display_name,
      p.enabled,
      p.expires_at,
      p.max_devices,
      coalesce(ds.active_devices,0) as active_devices,
      coalesce(ss.launch_count,0) as launch_count,
      ss.first_used_at,
      greatest(ss.session_last_seen_at, ds.device_last_seen_at) as last_seen_at,
      exists (
        select 1
        from public.listing_usage_sessions live
        where live.user_id = p.user_id
          and live.last_seen_at >= now() - interval '150 seconds'
          and (live.ended_at is null or live.ended_at >= now() - interval '150 seconds')
      ) as online,
      coalesce(es.listing_prepare_started,0) as listing_prepare_started,
      coalesce(es.listing_prepare_completed,0) as listing_prepare_completed,
      coalesce(es.listing_prepare_failed,0) as listing_prepare_failed,
      coalesce(es.listing_execute_started,0) as listing_execute_started,
      coalesce(es.listing_execute_completed,0) as listing_execute_completed,
      coalesce(es.listing_execute_failed,0) as listing_execute_failed,
      coalesce(es.batch_prepare_started,0) as batch_prepare_started,
      coalesce(es.batch_prepare_completed,0) as batch_prepare_completed,
      coalesce(es.batch_prepare_failed,0) as batch_prepare_failed,
      coalesce(es.batch_execute_started,0) as batch_execute_started,
      coalesce(es.batch_execute_completed,0) as batch_execute_completed,
      coalesce(es.batch_execute_failed,0) as batch_execute_failed,
      coalesce(ast.activity_24h, '[]'::jsonb) as activity_24h,
      (
        select s.app_version
        from public.listing_usage_sessions s
        where s.user_id = p.user_id and s.app_version <> ''
        order by s.last_seen_at desc
        limit 1
      ) as latest_app_version
    from public.download_portal_users p
    join auth.users u on u.id = p.user_id
    left join session_stats ss on ss.user_id = p.user_id
    left join event_stats es on es.user_id = p.user_id
    left join device_stats ds on ds.user_id = p.user_id
    left join activity_stats ast on ast.user_id = p.user_id
  )
  select jsonb_build_object(
    'generated_at', now(),
    'online_window_seconds', 150,
    'activity_window_hours', 24,
    'users', coalesce(
      jsonb_agg(
        jsonb_build_object(
          'user_id', user_id,
          'email', email,
          'display_name', display_name,
          'enabled', enabled,
          'expires_at', expires_at,
          'online', online,
          'first_used_at', first_used_at,
          'last_seen_at', last_seen_at,
          'launch_count', launch_count,
          'listing_prepare_started', listing_prepare_started,
          'listing_prepare_completed', listing_prepare_completed,
          'listing_prepare_failed', listing_prepare_failed,
          'listing_execute_started', listing_execute_started,
          'listing_execute_completed', listing_execute_completed,
          'listing_execute_failed', listing_execute_failed,
          'batch_prepare_started', batch_prepare_started,
          'batch_prepare_completed', batch_prepare_completed,
          'batch_prepare_failed', batch_prepare_failed,
          'batch_execute_started', batch_execute_started,
          'batch_execute_completed', batch_execute_completed,
          'batch_execute_failed', batch_execute_failed,
          'active_devices', active_devices,
          'max_devices', max_devices,
          'latest_app_version', coalesce(latest_app_version,''),
          'activity_24h', activity_24h
        )
        order by online desc, last_seen_at desc nulls last, email
      ),
      '[]'::jsonb
    )
  ) into payload
  from rows;

  return payload;
end;
$$;

revoke all on function public.get_listing_usage_admin_snapshot(uuid) from public, anon, authenticated;
grant execute on function public.get_listing_usage_admin_snapshot(uuid) to service_role;
