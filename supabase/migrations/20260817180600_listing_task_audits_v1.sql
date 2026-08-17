create table if not exists public.listing_task_audits (
  id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  session_id uuid references public.listing_usage_sessions(id) on delete set null,
  device_id text not null,
  app_version text not null default '',
  task_kind text not null,
  phase text not null default '',
  status text not null default 'running',
  product_url text not null default '',
  input_data jsonb not null default '{}'::jsonb,
  result_data jsonb not null default '{}'::jsonb,
  error_text text not null default '',
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  constraint listing_task_audits_device_id_length
    check (char_length(device_id) between 32 and 128),
  constraint listing_task_audits_kind
    check (task_kind in ('single','batch')),
  constraint listing_task_audits_status
    check (status in ('running','completed','failed','cancelled','review','ready')),
  constraint listing_task_audits_input_object
    check (jsonb_typeof(input_data) = 'object'),
  constraint listing_task_audits_result_object
    check (jsonb_typeof(result_data) = 'object')
);

create index if not exists listing_task_audits_user_time_idx
  on public.listing_task_audits (user_id, created_at desc);
create index if not exists listing_task_audits_time_idx
  on public.listing_task_audits (created_at desc);
create index if not exists listing_task_audits_status_time_idx
  on public.listing_task_audits (status, updated_at desc);

alter table public.listing_task_audits enable row level security;
revoke all on table public.listing_task_audits from anon, authenticated;
grant all on table public.listing_task_audits to service_role;

drop policy if exists "listing task audits deny direct client access"
  on public.listing_task_audits;
create policy "listing task audits deny direct client access"
on public.listing_task_audits
for all
to public
using (false)
with check (false);
