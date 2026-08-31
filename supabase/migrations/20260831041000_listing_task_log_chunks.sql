-- Complete sanitized task logs are transported separately from compact task audits.
-- Chunking removes the single-request size ceiling while preserving per-log SHA-256
-- integrity and deterministic reconstruction order.

create table if not exists public.listing_task_log_chunks (
  audit_id uuid not null references public.listing_task_audits(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  session_id uuid references public.listing_usage_sessions(id) on delete set null,
  device_id text not null,
  app_version text not null default '',
  log_name text not null,
  stage text not null default '',
  log_sha256 text not null,
  encoding text not null default 'gzip+base64-chunks',
  chunk_index integer not null,
  chunk_count integer not null,
  line_count integer not null default 0,
  byte_count bigint not null default 0,
  compressed_byte_count bigint not null default 0,
  chunk_data text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (audit_id, log_name, log_sha256, chunk_index),
  constraint listing_task_log_chunks_device_id_length
    check (char_length(device_id) between 32 and 128),
  constraint listing_task_log_chunks_name_length
    check (char_length(log_name) between 1 and 240),
  constraint listing_task_log_chunks_sha256
    check (log_sha256 ~ '^[0-9a-f]{64}$'),
  constraint listing_task_log_chunks_chunk_bounds
    check (chunk_index >= 0 and chunk_count >= 1 and chunk_index < chunk_count),
  constraint listing_task_log_chunks_encoding
    check (encoding = 'gzip+base64-chunks')
);

create index if not exists listing_task_log_chunks_audit_idx
  on public.listing_task_log_chunks (audit_id, log_name, log_sha256, chunk_index);
create index if not exists listing_task_log_chunks_user_time_idx
  on public.listing_task_log_chunks (user_id, updated_at desc);

alter table public.listing_task_log_chunks enable row level security;
revoke all on table public.listing_task_log_chunks from anon, authenticated;
grant all on table public.listing_task_log_chunks to service_role;

drop policy if exists "listing task log chunks deny direct client access"
  on public.listing_task_log_chunks;
create policy "listing task log chunks deny direct client access"
on public.listing_task_log_chunks
for all
to public
using (false)
with check (false);

comment on table public.listing_task_log_chunks is
  'Lossless sanitized task log chunks uploaded after the compact listing_task_audits row.';
