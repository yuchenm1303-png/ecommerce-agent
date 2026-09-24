create index if not exists listing_task_audits_updated_at_idx
  on public.listing_task_audits (updated_at desc);

create index if not exists listing_system_samples_occurred_at_idx
  on public.listing_system_samples (occurred_at desc);

create index if not exists listing_usage_sessions_started_at_idx
  on public.listing_usage_sessions (started_at desc, user_id);

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
    where user_id = p_caller
      and is_admin = true
      and enabled = true
  ) then
    raise exception 'not_authorized' using errcode = '42501';
  end if;

  with
  bounds as (
    select
      date_trunc('hour', now()) - interval '23 hours' as start_hour,
      date_trunc('hour', now()) as end_hour
  ),
  hours as (
    select generate_series(
      b.start_hour,
      b.end_hour,
      interval '1 hour'
    ) as bucket_start
    from bounds b
  ),
  portal_users as (
    select
      p.user_id,
      u.email,
      coalesce(nullif(p.display_name, ''), u.email, '') as display_name,
      p.enabled,
      p.expires_at,
      p.max_devices
    from public.download_portal_users p
    join auth.users u on u.id = p.user_id
  ),
  session_stats as (
    select
      s.user_id,
      count(*)::bigint as launch_count,
      min(s.started_at) as first_used_at,
      max(s.last_seen_at) as session_last_seen_at
    from public.listing_usage_sessions s
    group by s.user_id
  ),
  event_stats as (
    select
      e.user_id,
      count(*) filter (where e.event_type = 'listing_prepare' and e.outcome = 'started')::bigint as listing_prepare_started,
      count(*) filter (where e.event_type = 'listing_prepare' and e.outcome = 'completed')::bigint as listing_prepare_completed,
      count(*) filter (where e.event_type = 'listing_prepare' and e.outcome = 'failed')::bigint as listing_prepare_failed,
      count(*) filter (where e.event_type = 'listing_execute' and e.outcome = 'started')::bigint as listing_execute_started,
      count(*) filter (where e.event_type = 'listing_execute' and e.outcome = 'completed')::bigint as listing_execute_completed,
      count(*) filter (where e.event_type = 'listing_execute' and e.outcome = 'failed')::bigint as listing_execute_failed,
      count(*) filter (where e.event_type = 'batch_prepare' and e.outcome = 'started')::bigint as batch_prepare_started,
      count(*) filter (where e.event_type = 'batch_prepare' and e.outcome = 'completed')::bigint as batch_prepare_completed,
      count(*) filter (where e.event_type = 'batch_prepare' and e.outcome = 'failed')::bigint as batch_prepare_failed,
      count(*) filter (where e.event_type = 'batch_execute' and e.outcome = 'started')::bigint as batch_execute_started,
      count(*) filter (where e.event_type = 'batch_execute' and e.outcome = 'completed')::bigint as batch_execute_completed,
      count(*) filter (where e.event_type = 'batch_execute' and e.outcome = 'failed')::bigint as batch_execute_failed
    from public.listing_usage_events e
    group by e.user_id
  ),
  device_stats as (
    select
      d.user_id,
      count(*) filter (where d.enabled = true and d.revoked_at is null)::bigint as active_devices,
      max(d.last_seen_at) as device_last_seen_at
    from public.download_portal_devices d
    group by d.user_id
  ),
  latest_session as (
    select distinct on (s.user_id)
      s.user_id,
      s.app_version,
      s.last_seen_at,
      s.ended_at
    from public.listing_usage_sessions s
    order by s.user_id, s.last_seen_at desc
  ),
  session_hourly as (
    select
      s.user_id,
      date_trunc('hour', s.started_at) as bucket_start,
      count(*)::bigint as launches
    from public.listing_usage_sessions s
    cross join bounds b
    where s.started_at >= b.start_hour
      and s.started_at < b.end_hour + interval '1 hour'
    group by s.user_id, date_trunc('hour', s.started_at)
  ),
  active_hourly as (
    select
      s.user_id,
      bucket.bucket_start
    from public.listing_usage_sessions s
    cross join bounds b
    cross join lateral generate_series(
      greatest(date_trunc('hour', s.started_at), b.start_hour),
      least(date_trunc('hour', coalesce(s.ended_at, s.last_seen_at)), b.end_hour),
      interval '1 hour'
    ) as bucket(bucket_start)
    where s.started_at < b.end_hour + interval '1 hour'
      and coalesce(s.ended_at, s.last_seen_at) >= b.start_hour
    group by s.user_id, bucket.bucket_start
  ),
  task_base as (
    select a.*
    from public.listing_task_audits a
    cross join bounds b
    where a.updated_at >= b.start_hour
      and a.updated_at < b.end_hour + interval '1 hour'
  ),
  native_rows as (
    select
      a.user_id,
      a.task_kind,
      a.status,
      a.started_at,
      a.completed_at,
      a.updated_at,
      a.created_at,
      coalesce(a.completed_at, a.updated_at, a.created_at) as event_at,
      case
        when a.task_kind = 'batch' then concat_ws(
          '|',
          'batch',
          a.user_id::text,
          coalesce(
            nullif(a.input_data->>'batch_id', ''),
            nullif(a.result_data->>'batch_id', ''),
            to_char(a.started_at at time zone 'UTC', 'YYYYMMDDHH24MISS')
          ),
          coalesce(
            nullif(a.input_data->>'job_id', ''),
            nullif(a.result_data->>'job_id', ''),
            md5(coalesce(a.product_url, ''))
          )
        )
        else concat_ws(
          '|',
          'single',
          a.user_id::text,
          coalesce(
            nullif(a.result_data->>'run_id', ''),
            nullif(a.input_data->>'run_id', ''),
            a.id::text
          )
        )
      end as logical_key
    from task_base a
    where a.task_kind = 'single'
       or (
         a.task_kind = 'batch'
         and (
           coalesce(a.input_data->>'audit_scope', '') = 'batch_link'
           or coalesce(a.result_data->>'audit_scope', '') = 'batch_link'
           or (
             coalesce(a.input_data->>'job_id', '') <> ''
             and coalesce(a.product_url, '') <> ''
           )
         )
       )
  ),
  legacy_base as (
    select
      a.*,
      greatest(
        case when jsonb_typeof(a.input_data->'items') = 'array' then jsonb_array_length(a.input_data->'items') else 0 end,
        case when jsonb_typeof(a.result_data->'jobs') = 'array' then jsonb_array_length(a.result_data->'jobs') else 0 end,
        case when coalesce(a.input_data->>'item_count', '') ~ '^\d+$' then (a.input_data->>'item_count')::integer else 0 end,
        case when coalesce(a.result_data->>'job_count', '') ~ '^\d+$' then (a.result_data->>'job_count')::integer else 0 end
      ) as child_count
    from task_base a
    where a.task_kind = 'batch'
      and not (
        coalesce(a.input_data->>'audit_scope', '') = 'batch_link'
        or coalesce(a.result_data->>'audit_scope', '') = 'batch_link'
        or (
          coalesce(a.input_data->>'job_id', '') <> ''
          and coalesce(a.product_url, '') <> ''
        )
      )
  ),
  legacy_rows as (
    select
      a.user_id,
      'batch'::text as task_kind,
      case
        when lower(a.phase) = 'batch_execute' then
          case upper(coalesce(job.value->>'status', ''))
            when 'DONE' then 'completed'
            when 'REVIEW' then 'review'
            when 'FAILED' then 'failed'
            when 'STOPPED' then 'cancelled'
            else a.status
          end
        else
          case upper(coalesce(job.value->>'status', ''))
            when 'READY' then 'ready'
            when 'REVIEW' then 'review'
            when 'FAILED' then 'failed'
            when 'STOPPED' then 'cancelled'
            else a.status
          end
      end as status,
      a.started_at,
      a.completed_at,
      a.updated_at,
      a.created_at,
      coalesce(
        nullif(job.value->>'updated_at', '')::timestamptz,
        a.completed_at,
        a.updated_at,
        a.created_at
      ) as event_at,
      concat_ws(
        '|',
        'legacy-batch',
        a.user_id::text,
        a.id::text,
        coalesce(nullif(job.value->>'job_id', ''), 'JOB-' || lpad((series.idx + 1)::text, 3, '0'))
      ) as logical_key
    from legacy_base a
    cross join lateral generate_series(0, greatest(a.child_count - 1, 0)) as series(idx)
    left join lateral (
      select coalesce(a.result_data->'jobs'->series.idx, '{}'::jsonb) as value
    ) job on true
    where a.child_count > 0
  ),
  logical_raw as (
    select * from native_rows
    union all
    select * from legacy_rows
  ),
  ranked_tasks as (
    select
      r.*,
      row_number() over (
        partition by r.logical_key
        order by r.updated_at desc nulls last, r.created_at desc nulls last
      ) as rn
    from logical_raw r
  ),
  logical_tasks as (
    select r.*
    from ranked_tasks r
    where r.rn = 1
  ),
  task_hourly as (
    select
      t.user_id,
      date_trunc('hour', t.event_at) as bucket_start,
      count(*) filter (where t.status in ('completed', 'ready'))::bigint as completed,
      count(*) filter (where t.status in ('failed', 'cancelled'))::bigint as failed
    from logical_tasks t
    cross join bounds b
    where t.event_at >= b.start_hour
      and t.event_at < b.end_hour + interval '1 hour'
    group by t.user_id, date_trunc('hour', t.event_at)
  ),
  activity_stats as (
    select
      p.user_id,
      jsonb_agg(
        jsonb_build_object(
          'bucket_start', h.bucket_start,
          'active', (a.user_id is not null),
          'launches', coalesce(s.launches, 0),
          'completed', coalesce(t.completed, 0),
          'failed', coalesce(t.failed, 0)
        )
        order by h.bucket_start
      ) as activity_24h
    from portal_users p
    cross join hours h
    left join active_hourly a
      on a.user_id = p.user_id
     and a.bucket_start = h.bucket_start
    left join session_hourly s
      on s.user_id = p.user_id
     and s.bucket_start = h.bucket_start
    left join task_hourly t
      on t.user_id = p.user_id
     and t.bucket_start = h.bucket_start
    group by p.user_id
  ),
  rows as (
    select
      p.user_id,
      p.email,
      p.display_name,
      p.enabled,
      p.expires_at,
      p.max_devices,
      coalesce(ds.active_devices, 0) as active_devices,
      coalesce(ss.launch_count, 0) as launch_count,
      ss.first_used_at,
      greatest(ss.session_last_seen_at, ds.device_last_seen_at) as last_seen_at,
      (
        ls.last_seen_at >= now() - interval '150 seconds'
        and (ls.ended_at is null or ls.ended_at >= now() - interval '150 seconds')
      ) as online,
      coalesce(es.listing_prepare_started, 0) as listing_prepare_started,
      coalesce(es.listing_prepare_completed, 0) as listing_prepare_completed,
      coalesce(es.listing_prepare_failed, 0) as listing_prepare_failed,
      coalesce(es.listing_execute_started, 0) as listing_execute_started,
      coalesce(es.listing_execute_completed, 0) as listing_execute_completed,
      coalesce(es.listing_execute_failed, 0) as listing_execute_failed,
      coalesce(es.batch_prepare_started, 0) as batch_prepare_started,
      coalesce(es.batch_prepare_completed, 0) as batch_prepare_completed,
      coalesce(es.batch_prepare_failed, 0) as batch_prepare_failed,
      coalesce(es.batch_execute_started, 0) as batch_execute_started,
      coalesce(es.batch_execute_completed, 0) as batch_execute_completed,
      coalesce(es.batch_execute_failed, 0) as batch_execute_failed,
      coalesce(ast.activity_24h, '[]'::jsonb) as activity_24h,
      coalesce(ls.app_version, '') as latest_app_version
    from portal_users p
    left join session_stats ss on ss.user_id = p.user_id
    left join event_stats es on es.user_id = p.user_id
    left join device_stats ds on ds.user_id = p.user_id
    left join latest_session ls on ls.user_id = p.user_id
    left join activity_stats ast on ast.user_id = p.user_id
  )
  select jsonb_build_object(
    'generated_at', now(),
    'online_window_seconds', 150,
    'activity_window_hours', 24,
    'activity_basis', 'database_hourly_independent_product_audits',
    'users',
      coalesce(
        jsonb_agg(
          jsonb_build_object(
            'user_id', r.user_id,
            'email', r.email,
            'display_name', r.display_name,
            'enabled', r.enabled,
            'expires_at', r.expires_at,
            'online', coalesce(r.online, false),
            'first_used_at', r.first_used_at,
            'last_seen_at', r.last_seen_at,
            'launch_count', r.launch_count,
            'listing_prepare_started', r.listing_prepare_started,
            'listing_prepare_completed', r.listing_prepare_completed,
            'listing_prepare_failed', r.listing_prepare_failed,
            'listing_execute_started', r.listing_execute_started,
            'listing_execute_completed', r.listing_execute_completed,
            'listing_execute_failed', r.listing_execute_failed,
            'batch_prepare_started', r.batch_prepare_started,
            'batch_prepare_completed', r.batch_prepare_completed,
            'batch_prepare_failed', r.batch_prepare_failed,
            'batch_execute_started', r.batch_execute_started,
            'batch_execute_completed', r.batch_execute_completed,
            'batch_execute_failed', r.batch_execute_failed,
            'active_devices', r.active_devices,
            'max_devices', r.max_devices,
            'latest_app_version', r.latest_app_version,
            'activity_24h', r.activity_24h
          )
          order by coalesce(r.online, false) desc, r.last_seen_at desc nulls last, r.email
        ),
        '[]'::jsonb
      )
  )
  into payload
  from rows r;

  return payload;
end;
$$;

revoke all on function public.get_listing_usage_admin_snapshot(uuid)
  from public, anon, authenticated;
grant execute on function public.get_listing_usage_admin_snapshot(uuid)
  to service_role;

create or replace function public.get_listing_usage_system_samples(
  p_caller uuid,
  p_hours integer default 24,
  p_bucket_minutes integer default 5
)
returns table (
  id bigint,
  user_id uuid,
  session_id uuid,
  device_id text,
  app_version text,
  sample jsonb,
  occurred_at timestamptz
)
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  v_hours integer := greatest(1, least(coalesce(p_hours, 24), 168));
  v_bucket_minutes integer := greatest(1, least(coalesce(p_bucket_minutes, 5), 60));
begin
  if p_caller is null or not exists (
    select 1
    from public.download_portal_users
    where user_id = p_caller
      and is_admin = true
      and enabled = true
  ) then
    raise exception 'not_authorized' using errcode = '42501';
  end if;

  return query
  with ranked as (
    select
      s.id as sample_id,
      s.user_id as sample_user_id,
      s.session_id as sample_session_id,
      s.device_id as sample_device_id,
      s.app_version as sample_app_version,
      s.sample as sample_payload,
      s.occurred_at as sample_occurred_at,
      row_number() over (
        partition by
          s.user_id,
          s.device_id,
          floor(extract(epoch from s.occurred_at) / (v_bucket_minutes * 60))
        order by s.occurred_at desc, s.id desc
      ) as rn
    from public.listing_system_samples s
    where s.occurred_at >= now() - make_interval(hours => v_hours)
  )
  select
    r.sample_id,
    r.sample_user_id,
    r.sample_session_id,
    r.sample_device_id,
    r.sample_app_version,
    r.sample_payload,
    r.sample_occurred_at
  from ranked r
  where r.rn = 1
  order by r.sample_occurred_at desc;
end;
$$;

revoke all on function public.get_listing_usage_system_samples(uuid, integer, integer)
  from public, anon, authenticated;
grant execute on function public.get_listing_usage_system_samples(uuid, integer, integer)
  to service_role;
