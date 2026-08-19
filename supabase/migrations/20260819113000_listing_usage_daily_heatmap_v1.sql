create or replace function public.get_listing_usage_daily_heatmap(
  p_caller uuid,
  p_days integer default 365
)
returns jsonb
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  v_days integer := greatest(14, least(coalesce(p_days, 365), 730));
  payload jsonb;
begin
  if p_caller is null or not exists (
    select 1
    from public.download_portal_users
    where user_id = p_caller and is_admin = true and enabled = true
  ) then
    raise exception 'not_authorized' using errcode = '42501';
  end if;

  with bounds as (
    select
      (now() at time zone 'Asia/Shanghai')::date - (v_days - 1) as start_day,
      (now() at time zone 'Asia/Shanghai')::date as end_day
  ),
  days as (
    select generate_series(b.start_day, b.end_day, interval '1 day')::date as day
    from bounds b
  ),
  base as (
    select a.*
    from public.listing_task_audits a
    cross join bounds b
    where a.updated_at >= (b.start_day::timestamp at time zone 'Asia/Shanghai')
      and a.updated_at < ((b.end_day + 1)::timestamp at time zone 'Asia/Shanghai')
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
        when a.task_kind = 'batch' then concat_ws('|',
          'batch',
          a.user_id::text,
          coalesce(
            nullif(a.input_data->>'batch_id',''),
            nullif(a.result_data->>'batch_id',''),
            to_char(a.started_at at time zone 'UTC', 'YYYYMMDDHH24MISS')
          ),
          coalesce(
            nullif(a.input_data->>'job_id',''),
            nullif(a.result_data->>'job_id',''),
            md5(coalesce(a.product_url,''))
          )
        )
        else concat_ws('|',
          'single',
          a.user_id::text,
          coalesce(
            nullif(a.result_data->>'run_id',''),
            nullif(a.input_data->>'run_id',''),
            a.id::text
          )
        )
      end as logical_key
    from base a
    where a.task_kind = 'single'
       or (
         a.task_kind = 'batch'
         and (
           coalesce(a.input_data->>'audit_scope','') = 'batch_link'
           or coalesce(a.result_data->>'audit_scope','') = 'batch_link'
           or (
             coalesce(a.input_data->>'job_id','') <> ''
             and coalesce(a.product_url,'') <> ''
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
        case when coalesce(a.input_data->>'item_count','') ~ '^\d+$' then (a.input_data->>'item_count')::integer else 0 end,
        case when coalesce(a.result_data->>'job_count','') ~ '^\d+$' then (a.result_data->>'job_count')::integer else 0 end
      ) as child_count
    from base a
    where a.task_kind = 'batch'
      and not (
        coalesce(a.input_data->>'audit_scope','') = 'batch_link'
        or coalesce(a.result_data->>'audit_scope','') = 'batch_link'
        or (
          coalesce(a.input_data->>'job_id','') <> ''
          and coalesce(a.product_url,'') <> ''
        )
      )
  ),
  legacy_rows as (
    select
      a.user_id,
      'batch'::text as task_kind,
      case
        when lower(a.phase) = 'batch_execute' then
          case upper(coalesce(job.value->>'status',''))
            when 'DONE' then 'completed'
            when 'REVIEW' then 'review'
            when 'FAILED' then 'failed'
            when 'STOPPED' then 'cancelled'
            else a.status
          end
        else
          case upper(coalesce(job.value->>'status',''))
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
        nullif(job.value->>'updated_at','')::timestamptz,
        a.completed_at,
        a.updated_at,
        a.created_at
      ) as event_at,
      concat_ws('|',
        'legacy-batch',
        a.user_id::text,
        a.id::text,
        coalesce(nullif(job.value->>'job_id',''), 'JOB-' || lpad((g.idx + 1)::text, 3, '0'))
      ) as logical_key
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
  ),
  logical_tasks as (
    select *
    from ranked
    where rn = 1
  ),
  task_daily as (
    select
      (t.event_at at time zone 'Asia/Shanghai')::date as day,
      count(*)::bigint as tasks,
      count(*) filter (where t.status in ('completed','ready'))::bigint as success,
      count(*) filter (where t.status in ('failed','cancelled'))::bigint as failed,
      count(*) filter (where t.status = 'review')::bigint as review,
      count(*) filter (where t.status = 'running')::bigint as running,
      count(*) filter (where t.task_kind = 'single')::bigint as single_tasks,
      count(*) filter (where t.task_kind = 'batch')::bigint as batch_tasks
    from logical_tasks t
    group by 1
  ),
  launch_daily as (
    select
      (s.started_at at time zone 'Asia/Shanghai')::date as day,
      count(*)::bigint as launches
    from public.listing_usage_sessions s
    cross join bounds b
    where s.started_at >= (b.start_day::timestamp at time zone 'Asia/Shanghai')
      and s.started_at < ((b.end_day + 1)::timestamp at time zone 'Asia/Shanghai')
    group by 1
  ),
  crash_daily as (
    select
      (d.created_at at time zone 'Asia/Shanghai')::date as day,
      count(*)::bigint as crashes
    from public.listing_diagnostic_reports d
    cross join bounds b
    where d.created_at >= (b.start_day::timestamp at time zone 'Asia/Shanghai')
      and d.created_at < ((b.end_day + 1)::timestamp at time zone 'Asia/Shanghai')
    group by 1
  ),
  active_rows as (
    select
      (t.event_at at time zone 'Asia/Shanghai')::date as day,
      t.user_id
    from logical_tasks t
    union
    select
      (s.started_at at time zone 'Asia/Shanghai')::date as day,
      s.user_id
    from public.listing_usage_sessions s
    cross join bounds b
    where s.started_at >= (b.start_day::timestamp at time zone 'Asia/Shanghai')
      and s.started_at < ((b.end_day + 1)::timestamp at time zone 'Asia/Shanghai')
  ),
  active_daily as (
    select day, count(distinct user_id)::bigint as active_accounts
    from active_rows
    group by day
  )
  select jsonb_build_object(
    'timezone', 'Asia/Shanghai',
    'window_days', v_days,
    'generated_at', now(),
    'days', jsonb_agg(
      jsonb_build_object(
        'date', to_char(d.day, 'YYYY-MM-DD'),
        'tasks', coalesce(t.tasks, 0),
        'success', coalesce(t.success, 0),
        'failed', coalesce(t.failed, 0),
        'review', coalesce(t.review, 0),
        'running', coalesce(t.running, 0),
        'single', coalesce(t.single_tasks, 0),
        'batch', coalesce(t.batch_tasks, 0),
        'active_accounts', coalesce(a.active_accounts, 0),
        'launches', coalesce(l.launches, 0),
        'crashes', coalesce(c.crashes, 0)
      )
      order by d.day
    )
  ) into payload
  from days d
  left join task_daily t on t.day = d.day
  left join launch_daily l on l.day = d.day
  left join crash_daily c on c.day = d.day
  left join active_daily a on a.day = d.day;

  return payload;
end;
$$;

revoke all on function public.get_listing_usage_daily_heatmap(uuid, integer) from public, anon, authenticated;
grant execute on function public.get_listing_usage_daily_heatmap(uuid, integer) to service_role;
