create table if not exists public.listing_system_samples (
  id bigserial primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  session_id uuid not null references public.listing_usage_sessions(id) on delete cascade,
  device_id text not null,
  app_version text not null default '',
  sample jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  constraint listing_system_samples_device_id_length
    check (char_length(device_id) between 32 and 128),
  constraint listing_system_samples_sample_object
    check (jsonb_typeof(sample) = 'object')
);

create index if not exists listing_system_samples_user_time_idx
  on public.listing_system_samples (user_id, occurred_at desc);
create index if not exists listing_system_samples_session_time_idx
  on public.listing_system_samples (session_id, occurred_at desc);
create index if not exists listing_system_samples_device_time_idx
  on public.listing_system_samples (device_id, occurred_at desc);
create index if not exists listing_system_samples_version_time_idx
  on public.listing_system_samples (app_version, occurred_at desc);

alter table public.listing_system_samples enable row level security;
revoke all on table public.listing_system_samples from anon, authenticated;
grant all on table public.listing_system_samples to service_role;

drop policy if exists "listing system samples deny direct client access"
  on public.listing_system_samples;
create policy "listing system samples deny direct client access"
on public.listing_system_samples
for all
to public
using (false)
with check (false);
