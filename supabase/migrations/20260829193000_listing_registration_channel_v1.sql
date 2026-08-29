create table if not exists public.listing_registration_sources (
  user_id uuid primary key references auth.users(id) on delete cascade,
  channel text not null,
  created_at timestamptz not null default now(),
  constraint listing_registration_sources_channel
    check (channel in ('first-client', 'direct'))
);

alter table public.listing_registration_sources enable row level security;

revoke all on table public.listing_registration_sources from anon, authenticated;
grant all on table public.listing_registration_sources to service_role;

drop policy if exists "listing registration sources deny direct client access"
  on public.listing_registration_sources;
create policy "listing registration sources deny direct client access"
on public.listing_registration_sources
for all
to public
using (false)
with check (false);

create or replace function public.capture_listing_registration_source()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  source_channel text;
  target_tenant_id uuid;
begin
  source_channel := lower(btrim(coalesce(new.raw_user_meta_data ->> 'registration_channel', 'direct')));
  if source_channel not in ('first-client', 'direct') then
    source_channel := 'direct';
  end if;

  insert into public.listing_registration_sources (user_id, channel)
  values (new.id, source_channel)
  on conflict (user_id) do nothing;

  if source_channel = 'first-client' then
    select t.id
      into target_tenant_id
    from public.listing_monitor_tenants t
    where t.slug = 'first-client'
      and t.enabled = true
    limit 1;

    if target_tenant_id is not null then
      insert into public.listing_monitor_tenant_members (
        tenant_id,
        user_id,
        role,
        enabled
      ) values (
        target_tenant_id,
        new.id,
        'member',
        true
      )
      on conflict (tenant_id, user_id) do update
      set enabled = true,
          updated_at = now();
    end if;
  end if;

  return new;
end;
$$;

revoke all on function public.capture_listing_registration_source() from public, anon, authenticated;

drop trigger if exists listing_capture_registration_source on auth.users;
create trigger listing_capture_registration_source
after insert on auth.users
for each row
execute function public.capture_listing_registration_source();
