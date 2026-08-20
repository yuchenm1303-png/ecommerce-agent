create extension if not exists pg_cron with schema extensions;

create index if not exists listing_task_audits_event_at_idx
  on public.listing_task_audits ((coalesce(completed_at, updated_at, created_at)) desc);

create index if not exists listing_task_audits_user_event_at_idx
  on public.listing_task_audits (user_id, (coalesce(completed_at, updated_at, created_at)) desc);

create table if not exists public.listing_usage_hourly_rollups (
  bucket_start timestamptz not null,
  user_id uuid not null,
  active boolean not null default false,
  launches bigint not null default 0,
  completed bigint not null default 0,
  failed bigint not null default 0,
  refreshed_at timestamptz not null default now(),
  primary key (bucket_start, user_id)
);

create index if not exists listing_usage_hourly_rollups_user_time_idx
  on public.listing_usage_hourly_rollups (user_id, bucket_start desc);

alter table public.listing_usage_hourly_rollups enable row level security;
revoke all on public.listing_usage_hourly_rollups from anon, authenticated;
grant select on public.listing_usage_hourly_rollups to service_role;

create table if not exists public.listing_usage_daily_rollups (
  activity_date date not null,
  user_id uuid not null,
  tasks bigint not null default 0,
  success bigint not null default 0,
  failed bigint not null default 0,
  review bigint not null default 0,
  running bigint not null default 0,
  single_tasks bigint not null default 0,
  batch_tasks bigint not null default 0,
  launches bigint not null default 0,
  crashes bigint not null default 0,
  listing_prepare_started bigint not null default 0,
  listing_prepare_completed bigint not null default 0,
  listing_prepare_failed bigint not null default 0,
  listing_execute_started bigint not null default 0,
  listing_execute_completed bigint not null default 0,
  listing_execute_failed bigint not null default 0,
  batch_prepare_started bigint not null default 0,
  batch_prepare_completed bigint not null default 0,
  batch_prepare_failed bigint not null default 0,
  batch_execute_started bigint not null default 0,
  batch_execute_completed bigint not null default 0,
  batch_execute_failed bigint not null default 0,
  refreshed_at timestamptz not null default now(),
  primary key (activity_date, user_id)
);

create index if not exists listing_usage_daily_rollups_user_date_idx
  on public.listing_usage_daily_rollups (user_id, activity_date desc);

alter table public.listing_usage_daily_rollups enable row level security;
revoke all on public.listing_usage_daily_rollups from anon, authenticated;
grant select on public.listing_usage_daily_rollups to service_role;

create table if not exists public.listing_system_samples_5m (
  bucket_start timestamptz not null,
  user_id uuid not null,
  device_id text not null,
  source_id bigint not null,
  session_id uuid,
  app_version text not null default '',
  sample jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null,
  refreshed_at timestamptz not null default now(),
  primary key (bucket_start, user_id, device_id)
);

create index if not exists listing_system_samples_5m_time_idx
  on public.listing_system_samples_5m (bucket_start desc);
create index if not exists listing_system_samples_5m_user_device_time_idx
  on public.listing_system_samples_5m (user_id, device_id, bucket_start desc);

alter table public.listing_system_samples_5m enable row level security;
revoke all on public.listing_system_samples_5m from anon, authenticated;
grant select on public.listing_system_samples_5m to service_role;

create or replace function private.listing_usage_logical_tasks(
  p_from timestamptz,
  p_to timestamptz
)
returns table (
  user_id uuid,
  task_kind text,
  status text,
  event_at timestamptz,
  logical_key text
)
language sql
stable
security invoker
set search_path = ''
as $$
  with base as (
    select a.*
    from public.listing_task_audits a
    where coalesce(a.completed_at, a.updated_at, a.created_at) >= p_from
      and coalesce(a.completed_at, a.updated_at, a.created_at) < p_to
  ),
  native_rows as (
    select
      a.user_id,
      a.task_kind,
      a.status,
      coalesce(a.completed_at, a.updated_at, a.created_at) as event_at,
      case
        when a.task_kind = 'batch' then concat_ws('|',
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
        else concat_ws('|',
          'single',
          a.user_id::text,
          coalesce(
            nullif(a.result_data->>'run_id', ''),
            nullif(a.input_data->>'run_id', ''),
            a.id::text
          )
        )
      end as logical_key,
      a.updated_at,
      a.created_at
    from base a
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
    from base a
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
      coalesce(
        nullif(job.value->>'updated_at', '')::timestamptz,
        a.completed_at,
        a.updated_at,
        a.created_at
      ) as event_at,
      concat_ws('|',
        'legacy-batch',
        a.user_id::text,
        a.id::text,
        coalesce(nullif(job.value->>'job_id', ''), 'JOB-' || lpad((g.idx + 1)::text, 3, '0'))
      ) as logical_key,
      a.updated_at,
      a.created_at
    from legacy_base a
    cross join lateral generate_series(0, greatest(a.child_count - 1, 0)) as g(idx)
    left join lateral (
      select coalesce(a.result_data->'jobs'->g.idx, '{}'::jsonb) as value
    ) job on true
    where a.child_count > 0
  ),
  logical_raw as (
    select * from native_rows
    union all
    select * from legacy_rows
  ),
  ranked as (
    select
      r.*,
      row_number() over (
        partition by r.logical_key
        order by r.updated_at desc nulls last, r.created_at desc nulls last
      ) as rn
    from logical_raw r
  )
  select r.user_id, r.task_kind, r.status, r.event_at, r.logical_key
  from ranked r
  where r.rn = 1
    and r.event_at >= p_from
    and r.event_at < p_to;
$$;

revoke all on function private.listing_usage_logical_tasks(timestamptz, timestamptz) from public, anon, authenticated;
grant execute on function private.listing_usage_logical_tasks(timestamptz, timestamptz) to service_role;

create or replace function public.refresh_listing_usage_read_model(
  p_from timestamptz default now() - interval '3 hours',
  p_to timestamptz default now() + interval '1 minute'
)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_from timestamptz := least(coalesce(p_from, now() - interval '3 hours'), coalesce(p_to, now()));
  v_to timestamptz := greatest(coalesce(p_to, now() + interval '1 minute'), coalesce(p_from, now()));
  v_hour_from timestamptz;
  v_hour_to timestamptz;
  v_day_from date;
  v_day_to date;
  v_day_from_utc timestamptz;
  v_day_to_utc timestamptz;
begin
  v_hour_from := date_trunc('hour', v_from);
  v_hour_to := date_trunc('hour', v_to) + interval '1 hour';
  v_day_from := (v_from at time zone 'Asia/Shanghai')::date;
  v_day_to := (v_to at time zone 'Asia/Shanghai')::date;
  v_day_from_utc := v_day_from::timestamp at time zone 'Asia/Shanghai';
  v_day_to_utc := (v_day_to + 1)::timestamp at time zone 'Asia/Shanghai';

  delete from public.listing_usage_hourly_rollups
  where bucket_start >= v_hour_from and bucket_start < v_hour_to;

  with
  hours as (
    select generate_series(v_hour_from, v_hour_to - interval '1 hour', interval '1 hour') as bucket_start
  ),
  task_hourly as (
    select
      t.user_id,
      date_trunc('hour', t.event_at) as bucket_start,
      count(*) filter (where t.status in ('completed', 'ready'))::bigint as completed,
      count(*) filter (where t.status in ('failed', 'cancelled'))::bigint as failed
    from private.listing_usage_logical_tasks(v_hour_from, v_hour_to) t
    group by t.user_id, date_trunc('hour', t.event_at)
  ),
  launch_hourly as (
    select
      s.user_id,
      date_trunc('hour', s.started_at) as bucket_start,
      count(*)::bigint as launches
    from public.listing_usage_sessions s
    where s.started_at >= v_hour_from and s.started_at < v_hour_to
    group by s.user_id, date_trunc('hour', s.started_at)
  ),
  active_hourly as (
    select
      s.user_id,
      g.bucket_start
    from public.listing_usage_sessions s
    cross join lateral generate_series(
      greatest(date_trunc('hour', s.started_at), v_hour_from),
      least(date_trunc('hour', coalesce(s.ended_at, s.last_seen_at)), v_hour_to - interval '1 hour'),
      interval '1 hour'
    ) as g(bucket_start)
    where s.started_at < v_hour_to
      and coalesce(s.ended_at, s.last_seen_at) >= v_hour_from
    group by s.user_id, g.bucket_start
  ),
  keys as (
    select user_id, bucket_start from task_hourly
    union
    select user_id, bucket_start from launch_hourly
    union
    select user_id, bucket_start from active_hourly
  )
  insert into public.listing_usage_hourly_rollups (
    bucket_start, user_id, active, launches, completed, failed, refreshed_at
  )
  select
    k.bucket_start,
    k.user_id,
    (a.user_id is not null),
    coalesce(l.launches, 0),
    coalesce(t.completed, 0),
    coalesce(t.failed, 0),
    now()
  from keys k
  left join active_hourly a on a.user_id = k.user_id and a.bucket_start = k.bucket_start
  left join launch_hourly l on l.user_id = k.user_id and l.bucket_start = k.bucket_start
  left join task_hourly t on t.user_id = k.user_id and t.bucket_start = k.bucket_start;

  delete from public.listing_usage_daily_rollups
  where activity_date between v_day_from and v_day_to;

  with
  task_daily as (
    select
      (t.event_at at time zone 'Asia/Shanghai')::date as activity_date,
      t.user_id,
      count(*)::bigint as tasks,
      count(*) filter (where t.status in ('completed', 'ready'))::bigint as success,
      count(*) filter (where t.status in ('failed', 'cancelled'))::bigint as failed,
      count(*) filter (where t.status = 'review')::bigint as review,
      count(*) filter (where t.status = 'running')::bigint as running,
      count(*) filter (where t.task_kind = 'single')::bigint as single_tasks,
      count(*) filter (where t.task_kind = 'batch')::bigint as batch_tasks
    from private.listing_usage_logical_tasks(v_day_from_utc, v_day_to_utc) t
    group by 1, 2
  ),
  launch_daily as (
    select
      (s.started_at at time zone 'Asia/Shanghai')::date as activity_date,
      s.user_id,
      count(*)::bigint as launches
    from public.listing_usage_sessions s
    where s.started_at >= v_day_from_utc and s.started_at < v_day_to_utc
    group by 1, 2
  ),
  crash_daily as (
    select
      (d.created_at at time zone 'Asia/Shanghai')::date as activity_date,
      d.user_id,
      count(*)::bigint as crashes
    from public.listing_diagnostic_reports d
    where d.created_at >= v_day_from_utc and d.created_at < v_day_to_utc
    group by 1, 2
  ),
  event_daily as (
    select
      (e.occurred_at at time zone 'Asia/Shanghai')::date as activity_date,
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
    where e.occurred_at >= v_day_from_utc and e.occurred_at < v_day_to_utc
    group by 1, 2
  ),
  keys as (
    select activity_date, user_id from task_daily
    union
    select activity_date, user_id from launch_daily
    union
    select activity_date, user_id from crash_daily
    union
    select activity_date, user_id from event_daily
  )
  insert into public.listing_usage_daily_rollups (
    activity_date, user_id,
    tasks, success, failed, review, running, single_tasks, batch_tasks,
    launches, crashes,
    listing_prepare_started, listing_prepare_completed, listing_prepare_failed,
    listing_execute_started, listing_execute_completed, listing_execute_failed,
    batch_prepare_started, batch_prepare_completed, batch_prepare_failed,
    batch_execute_started, batch_execute_completed, batch_execute_failed,
    refreshed_at
  )
  select
    k.activity_date,
    k.user_id,
    coalesce(t.tasks, 0), coalesce(t.success, 0), coalesce(t.failed, 0),
    coalesce(t.review, 0), coalesce(t.running, 0), coalesce(t.single_tasks, 0), coalesce(t.batch_tasks, 0),
    coalesce(l.launches, 0), coalesce(c.crashes, 0),
    coalesce(e.listing_prepare_started, 0), coalesce(e.listing_prepare_completed, 0), coalesce(e.listing_prepare_failed, 0),
    coalesce(e.listing_execute_started, 0), coalesce(e.listing_execute_completed, 0), coalesce(e.listing_execute_failed, 0),
    coalesce(e.batch_prepare_started, 0), coalesce(e.batch_prepare_completed, 0), coalesce(e.batch_prepare_failed, 0),
    coalesce(e.batch_execute_started, 0), coalesce(e.batch_execute_completed, 0), coalesce(e.batch_execute_failed, 0),
    now()
  from keys k
  left join task_daily t on t.activity_date = k.activity_date and t.user_id = k.user_id
  left join launch_daily l on l.activity_date = k.activity_date and l.user_id = k.user_id
  left join crash_daily c on c.activity_date = k.activity_date and c.user_id = k.user_id
  left join event_daily e on e.activity_date = k.activity_date and e.user_id = k.user_id;

  delete from public.listing_system_samples_5m
  where bucket_start >= to_timestamp(floor(extract(epoch from v_from) / 300) * 300)
    and bucket_start < to_timestamp(ceil(extract(epoch from v_to) / 300) * 300);

  with ranked as (
    select
      to_timestamp(floor(extract(epoch from s.occurred_at) / 300) * 300) as bucket_start,
      s.user_id,
      s.device_id,
      s.id as source_id,
      s.session_id,
      s.app_version,
      s.sample,
      s.occurred_at,
      row_number() over (
        partition by s.user_id, s.device_id, floor(extract(epoch from s.occurred_at) / 300)
        order by s.occurred_at desc, s.id desc
      ) as rn
    from public.listing_system_samples s
    where s.occurred_at >= v_from and s.occurred_at < v_to
  )
  insert into public.listing_system_samples_5m (
    bucket_start, user_id, device_id, source_id, session_id, app_version, sample, occurred_at, refreshed_at
  )
  select
    r.bucket_start, r.user_id, r.device_id, r.source_id, r.session_id,
    coalesce(r.app_version, ''), r.sample, r.occurred_at, now()
  from ranked r
  where r.rn = 1;
end;
$$;

revoke all on function public.refresh_listing_usage_read_model(timestamptz, timestamptz) from public, anon, authenticated;
grant execute on function public.refresh_listing_usage_read_model(timestamptz, timestamptz) to service_role;

create or replace function public.get_listing_usage_admin_snapshot(p_caller uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  payload jsonb;
begin
  if p_caller is null or not exists (
    select 1
    from public.download_portal_users dpu
    where dpu.user_id = p_caller
      and dpu.is_admin = true
      and dpu.enabled = true
  ) then
    raise exception 'not_authorized' using errcode = '42501';
  end if;

  with
  bounds as (
    select date_trunc('hour', now()) - interval '23 hours' as start_hour,
           date_trunc('hour', now()) as end_hour
  ),
  hours as (
    select generate_series(b.start_hour, b.end_hour, interval '1 hour') as bucket_start
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
  lifetime as (
    select
      r.user_id,
      sum(r.launches)::bigint as launch_count,
      sum(r.listing_prepare_started)::bigint as listing_prepare_started,
      sum(r.listing_prepare_completed)::bigint as listing_prepare_completed,
      sum(r.listing_prepare_failed)::bigint as listing_prepare_failed,
      sum(r.listing_execute_started)::bigint as listing_execute_started,
      sum(r.listing_execute_completed)::bigint as listing_execute_completed,
      sum(r.listing_execute_failed)::bigint as listing_execute_failed,
      sum(r.batch_prepare_started)::bigint as batch_prepare_started,
      sum(r.batch_prepare_completed)::bigint as batch_prepare_completed,
      sum(r.batch_prepare_failed)::bigint as batch_prepare_failed,
      sum(r.batch_execute_started)::bigint as batch_execute_started,
      sum(r.batch_execute_completed)::bigint as batch_execute_completed,
      sum(r.batch_execute_failed)::bigint as batch_execute_failed
    from public.listing_usage_daily_rollups r
    group by r.user_id
  ),
  device_stats as (
    select
      d.user_id,
      count(*) filter (where d.enabled = true and d.revoked_at is null)::bigint as active_devices,
      max(d.last_seen_at) as device_last_seen_at
    from public.download_portal_devices d
    group by d.user_id
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
      coalesce(lf.launch_count, 0) as launch_count,
      greatest(ls.last_seen_at, ds.device_last_seen_at) as last_seen_at,
      (
        ls.last_seen_at >= now() - interval '150 seconds'
        and (ls.ended_at is null or ls.ended_at >= now() - interval '150 seconds')
      ) as online,
      coalesce(ls.app_version, '') as latest_app_version,
      coalesce(lf.listing_prepare_started, 0) as listing_prepare_started,
      coalesce(lf.listing_prepare_completed, 0) as listing_prepare_completed,
      coalesce(lf.listing_prepare_failed, 0) as listing_prepare_failed,
      coalesce(lf.listing_execute_started, 0) as listing_execute_started,
      coalesce(lf.listing_execute_completed, 0) as listing_execute_completed,
      coalesce(lf.listing_execute_failed, 0) as listing_execute_failed,
      coalesce(lf.batch_prepare_started, 0) as batch_prepare_started,
      coalesce(lf.batch_prepare_completed, 0) as batch_prepare_completed,
      coalesce(lf.batch_prepare_failed, 0) as batch_prepare_failed,
      coalesce(lf.batch_execute_started, 0) as batch_execute_started,
      coalesce(lf.batch_execute_completed, 0) as batch_execute_completed,
      coalesce(lf.batch_execute_failed, 0) as batch_execute_failed,
      (
        select jsonb_agg(
          jsonb_build_object(
            'bucket_start', h.bucket_start,
            'active', coalesce(hr.active, false),
            'launches', coalesce(hr.launches, 0),
            'completed', coalesce(hr.completed, 0),
            'failed', coalesce(hr.failed, 0)
          ) order by h.bucket_start
        )
        from hours h
        left join public.listing_usage_hourly_rollups hr
          on hr.user_id = p.user_id and hr.bucket_start = h.bucket_start
      ) as activity_24h
    from portal_users p
    left join lifetime lf on lf.user_id = p.user_id
    left join device_stats ds on ds.user_id = p.user_id
    left join lateral (
      select s.app_version, s.last_seen_at, s.ended_at
      from public.listing_usage_sessions s
      where s.user_id = p.user_id
      order by s.last_seen_at desc
      limit 1
    ) ls on true
  )
  select jsonb_build_object(
    'generated_at', now(),
    'online_window_seconds', 150,
    'activity_window_hours', 24,
    'activity_basis', 'persistent_hourly_rollup_v3',
    'users', coalesce(jsonb_agg(
      jsonb_build_object(
        'user_id', r.user_id,
        'email', r.email,
        'display_name', r.display_name,
        'enabled', r.enabled,
        'expires_at', r.expires_at,
        'online', coalesce(r.online, false),
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
        'activity_24h', coalesce(r.activity_24h, '[]'::jsonb)
      ) order by coalesce(r.online, false) desc, r.last_seen_at desc nulls last, r.email
    ), '[]'::jsonb)
  ) into payload
  from rows r;

  return payload;
end;
$$;

revoke all on function public.get_listing_usage_admin_snapshot(uuid) from public, anon, authenticated;
grant execute on function public.get_listing_usage_admin_snapshot(uuid) to service_role;

create or replace function public.get_listing_usage_daily_heatmap(p_caller uuid, p_days integer default 365)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_days integer := greatest(14, least(coalesce(p_days, 365), 730));
  payload jsonb;
begin
  if p_caller is null or not exists (
    select 1
    from public.download_portal_users dpu
    where dpu.user_id = p_caller and dpu.is_admin = true and dpu.enabled = true
  ) then
    raise exception 'not_authorized' using errcode = '42501';
  end if;

  with bounds as (
    select (now() at time zone 'Asia/Shanghai')::date - (v_days - 1) as start_day,
           (now() at time zone 'Asia/Shanghai')::date as end_day
  ),
  days as (
    select generate_series(b.start_day, b.end_day, interval '1 day')::date as day
    from bounds b
  ),
  daily as (
    select
      r.activity_date as day,
      sum(r.tasks)::bigint as tasks,
      sum(r.success)::bigint as success,
      sum(r.failed)::bigint as failed,
      sum(r.review)::bigint as review,
      sum(r.running)::bigint as running,
      sum(r.single_tasks)::bigint as single_tasks,
      sum(r.batch_tasks)::bigint as batch_tasks,
      sum(r.launches)::bigint as launches,
      sum(r.crashes)::bigint as crashes,
      count(*) filter (where r.tasks > 0 or r.launches > 0)::bigint as active_accounts
    from public.listing_usage_daily_rollups r
    cross join bounds b
    where r.activity_date between b.start_day and b.end_day
    group by r.activity_date
  )
  select jsonb_build_object(
    'timezone', 'Asia/Shanghai',
    'window_days', v_days,
    'generated_at', now(),
    'basis', 'persistent_daily_rollup_v3',
    'days', jsonb_agg(jsonb_build_object(
      'date', to_char(d.day, 'YYYY-MM-DD'),
      'tasks', coalesce(x.tasks, 0),
      'success', coalesce(x.success, 0),
      'failed', coalesce(x.failed, 0),
      'review', coalesce(x.review, 0),
      'running', coalesce(x.running, 0),
      'single', coalesce(x.single_tasks, 0),
      'batch', coalesce(x.batch_tasks, 0),
      'active_accounts', coalesce(x.active_accounts, 0),
      'launches', coalesce(x.launches, 0),
      'crashes', coalesce(x.crashes, 0)
    ) order by d.day)
  ) into payload
  from days d
  left join daily x on x.day = d.day;

  return payload;
end;
$$;

revoke all on function public.get_listing_usage_daily_heatmap(uuid, integer) from public, anon, authenticated;
grant execute on function public.get_listing_usage_daily_heatmap(uuid, integer) to service_role;

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
set search_path = ''
as $$
declare
  v_hours integer := greatest(1, least(coalesce(p_hours, 24), 168));
begin
  if p_caller is null or not exists (
    select 1
    from public.download_portal_users dpu
    where dpu.user_id = p_caller and dpu.is_admin = true and dpu.enabled = true
  ) then
    raise exception 'not_authorized' using errcode = '42501';
  end if;

  return query
  select
    s.source_id as id,
    s.user_id,
    s.session_id,
    s.device_id,
    s.app_version,
    s.sample,
    s.occurred_at
  from public.listing_system_samples_5m s
  where s.bucket_start >= now() - make_interval(hours => v_hours)
  order by s.occurred_at desc;
end;
$$;

revoke all on function public.get_listing_usage_system_samples(uuid, integer, integer) from public, anon, authenticated;
grant execute on function public.get_listing_usage_system_samples(uuid, integer, integer) to service_role;

do $$
declare
  v_start timestamptz;
begin
  select least(
    coalesce((select min(started_at) from public.listing_usage_sessions), now()),
    coalesce((select min(occurred_at) from public.listing_usage_events), now()),
    coalesce((select min(coalesce(completed_at, updated_at, created_at)) from public.listing_task_audits), now()),
    coalesce((select min(created_at) from public.listing_diagnostic_reports), now()),
    coalesce((select min(occurred_at) from public.listing_system_samples), now())
  ) into v_start;

  perform public.refresh_listing_usage_read_model(v_start, now() + interval '1 minute');
end;
$$;

do $$
declare
  v_job_id bigint;
begin
  for v_job_id in select jobid from cron.job where jobname in (
    'listing-usage-read-model-v3',
    'listing-usage-read-model-reconcile-v3'
  ) loop
    perform cron.unschedule(v_job_id);
  end loop;
end;
$$;

select cron.schedule(
  'listing-usage-read-model-v3',
  '* * * * *',
  $$select public.refresh_listing_usage_read_model(now() - interval '3 hours', now() + interval '1 minute');$$
);

select cron.schedule(
  'listing-usage-read-model-reconcile-v3',
  '17 2 * * *',
  $$select public.refresh_listing_usage_read_model(now() - interval '8 days', now() + interval '1 minute');$$
);
