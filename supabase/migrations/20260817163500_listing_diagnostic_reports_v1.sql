create table if not exists public.listing_diagnostic_reports (
  id uuid primary key default gen_random_uuid(),
  report_code text not null unique,
  user_id uuid not null references auth.users(id) on delete cascade,
  device_id text not null,
  app_version text not null default '',
  crash_id text not null,
  startup_stage text not null default '',
  report jsonb not null,
  created_at timestamptz not null default now(),
  constraint listing_diagnostic_reports_device_id_length
    check (char_length(device_id) between 32 and 128),
  constraint listing_diagnostic_reports_code_length
    check (char_length(report_code) between 8 and 64),
  constraint listing_diagnostic_reports_crash_id_length
    check (char_length(crash_id) between 8 and 160),
  constraint listing_diagnostic_reports_report_object
    check (jsonb_typeof(report) = 'object')
);

create index if not exists listing_diagnostic_reports_user_time_idx
  on public.listing_diagnostic_reports (user_id, created_at desc);
create index if not exists listing_diagnostic_reports_device_time_idx
  on public.listing_diagnostic_reports (device_id, created_at desc);
create index if not exists listing_diagnostic_reports_crash_id_idx
  on public.listing_diagnostic_reports (crash_id);

alter table public.listing_diagnostic_reports enable row level security;
revoke all on table public.listing_diagnostic_reports from anon, authenticated;
grant all on table public.listing_diagnostic_reports to service_role;

drop policy if exists "listing diagnostic reports deny direct client access"
  on public.listing_diagnostic_reports;
create policy "listing diagnostic reports deny direct client access"
on public.listing_diagnostic_reports
for all
to public
using (false)
with check (false);
