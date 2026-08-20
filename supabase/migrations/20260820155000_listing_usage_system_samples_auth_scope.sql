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
    from public.download_portal_users dpu
    where dpu.user_id = p_caller
      and dpu.is_admin = true
      and dpu.enabled = true
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
