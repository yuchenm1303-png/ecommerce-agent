create table if not exists public.listing_monitor_tenants (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint listing_monitor_tenants_slug_format
    check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  constraint listing_monitor_tenants_name_length
    check (char_length(btrim(name)) between 1 and 120)
);

create table if not exists public.listing_monitor_tenant_members (
  tenant_id uuid not null references public.listing_monitor_tenants(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'member',
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, user_id),
  constraint listing_monitor_tenant_members_role
    check (role in ('admin', 'member'))
);

create index if not exists listing_monitor_tenant_members_user_idx
  on public.listing_monitor_tenant_members (user_id, enabled, role);

create unique index if not exists listing_monitor_tenant_single_admin_scope_idx
  on public.listing_monitor_tenant_members (user_id)
  where role = 'admin' and enabled = true;

alter table public.listing_monitor_tenants enable row level security;
alter table public.listing_monitor_tenant_members enable row level security;

revoke all on table public.listing_monitor_tenants from anon, authenticated;
revoke all on table public.listing_monitor_tenant_members from anon, authenticated;
grant all on table public.listing_monitor_tenants to service_role;
grant all on table public.listing_monitor_tenant_members to service_role;

drop policy if exists "listing monitor tenants deny direct client access" on public.listing_monitor_tenants;
create policy "listing monitor tenants deny direct client access"
on public.listing_monitor_tenants
for all
to public
using (false)
with check (false);

drop policy if exists "listing monitor tenant members deny direct client access" on public.listing_monitor_tenant_members;
create policy "listing monitor tenant members deny direct client access"
on public.listing_monitor_tenant_members
for all
to public
using (false)
with check (false);

create or replace function public.get_listing_monitor_scope(p_caller uuid)
returns table (
  scope_kind text,
  tenant_id uuid,
  tenant_name text,
  tenant_slug text,
  user_id uuid
)
language sql
security definer
set search_path = ''
as $$
  with caller as (
    select
      p.user_id,
      coalesce(nullif(p.display_name, ''), u.email, '') as display_name
    from public.download_portal_users p
    join auth.users u on u.id = p.user_id
    where p.user_id = p_caller
      and p.enabled = true
  ),
  admin_tenant as (
    select t.id, t.name, t.slug
    from public.listing_monitor_tenant_members m
    join public.listing_monitor_tenants t on t.id = m.tenant_id
    join caller c on c.user_id = m.user_id
    where m.role = 'admin'
      and m.enabled = true
      and t.enabled = true
    limit 1
  ),
  tenant_scope as (
    select
      'tenant'::text as scope_kind,
      t.id as tenant_id,
      t.name as tenant_name,
      t.slug as tenant_slug,
      m.user_id
    from admin_tenant t
    join public.listing_monitor_tenant_members m
      on m.tenant_id = t.id and m.enabled = true
  ),
  self_scope as (
    select
      'self'::text as scope_kind,
      null::uuid as tenant_id,
      c.display_name as tenant_name,
      'self'::text as tenant_slug,
      c.user_id
    from caller c
    where not exists (select 1 from admin_tenant)
  )
  select * from tenant_scope
  union all
  select * from self_scope;
$$;

revoke all on function public.get_listing_monitor_scope(uuid) from public, anon, authenticated;
grant execute on function public.get_listing_monitor_scope(uuid) to service_role;

create or replace function public.get_listing_usage_tenant_snapshot(p_caller uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  payload jsonb;
begin
  if p_caller is null or not exists (
    select 1 from public.get_listing_monitor_scope(p_caller) limit 1
  ) then
    raise exception 'not_authorized' using errcode = '42501';
  end if;

  with
  scope_users as (
    select distinct s.user_id
    from public.get_listing_monitor_scope(p_caller) s
  ),
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
    join scope_users su on su.user_id = p.user_id
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
    join scope_users su on su.user_id = r.user_id
    group by r.user_id
  ),
  device_stats as (
    select
      d.user_id,
      count(*) filter (where d.enabled = true and d.revoked_at is null)::bigint as active_devices,
      max(d.last_seen_at) as device_last_seen_at
    from public.download_portal_devices d
    join scope_users su on su.user_id = d.user_id
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
    'activity_basis', 'persistent_hourly_rollup_v3_tenant',
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

revoke all on function public.get_listing_usage_tenant_snapshot(uuid) from public, anon, authenticated;
grant execute on function public.get_listing_usage_tenant_snapshot(uuid) to service_role;

create or replace function public.get_listing_usage_tenant_daily_heatmap(p_caller uuid, p_days integer default 365)
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
    select 1 from public.get_listing_monitor_scope(p_caller) limit 1
  ) then
    raise exception 'not_authorized' using errcode = '42501';
  end if;

  with scope_users as (
    select distinct s.user_id from public.get_listing_monitor_scope(p_caller) s
  ),
  bounds as (
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
    join scope_users su on su.user_id = r.user_id
    cross join bounds b
    where r.activity_date between b.start_day and b.end_day
    group by r.activity_date
  )
  select jsonb_build_object(
    'timezone', 'Asia/Shanghai',
    'window_days', v_days,
    'generated_at', now(),
    'basis', 'persistent_daily_rollup_v3_tenant',
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

revoke all on function public.get_listing_usage_tenant_daily_heatmap(uuid, integer) from public, anon, authenticated;
grant execute on function public.get_listing_usage_tenant_daily_heatmap(uuid, integer) to service_role;

create or replace function public.get_listing_usage_tenant_system_samples(
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
    select 1 from public.get_listing_monitor_scope(p_caller) limit 1
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
  join public.get_listing_monitor_scope(p_caller) sc on sc.user_id = s.user_id
  where s.bucket_start >= now() - make_interval(hours => v_hours)
  order by s.occurred_at desc;
end;
$$;

revoke all on function public.get_listing_usage_tenant_system_samples(uuid, integer, integer) from public, anon, authenticated;
grant execute on function public.get_listing_usage_tenant_system_samples(uuid, integer, integer) to service_role;
