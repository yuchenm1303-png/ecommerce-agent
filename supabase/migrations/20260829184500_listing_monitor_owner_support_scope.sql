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
      coalesce(nullif(p.display_name, ''), u.email, '') as display_name,
      coalesce(p.is_admin, false) as platform_admin
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
  support_tenant_candidates as (
    select t.id, t.name, t.slug
    from public.listing_monitor_tenant_members m
    join public.listing_monitor_tenants t on t.id = m.tenant_id
    join caller c on c.user_id = m.user_id
    where c.platform_admin = true
      and m.enabled = true
      and t.enabled = true
  ),
  support_tenant as (
    select c.id, c.name, c.slug
    from support_tenant_candidates c
    where (select count(*) from support_tenant_candidates) = 1
    limit 1
  ),
  visible_tenant as (
    select a.id, a.name, a.slug from admin_tenant a
    union all
    select s.id, s.name, s.slug
    from support_tenant s
    where not exists (select 1 from admin_tenant)
  ),
  tenant_scope as (
    select
      'tenant'::text as scope_kind,
      t.id as tenant_id,
      t.name as tenant_name,
      t.slug as tenant_slug,
      m.user_id
    from visible_tenant t
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
    where not exists (select 1 from visible_tenant)
  )
  select * from tenant_scope
  union all
  select * from self_scope;
$$;

revoke all on function public.get_listing_monitor_scope(uuid) from public, anon, authenticated;
grant execute on function public.get_listing_monitor_scope(uuid) to service_role;
