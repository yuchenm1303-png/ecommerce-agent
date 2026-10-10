-- Persist and deduplicate retried client lifecycle events.
-- The nullable UUID is backward-compatible with clients that do not send IDs.
alter table public.listing_usage_events
  add column if not exists client_event_id uuid;
create unique index if not exists listing_usage_events_client_event_id_uniq
  on public.listing_usage_events (client_event_id);
create index if not exists listing_usage_events_user_event_time_idx
  on public.listing_usage_events (user_id, occurred_at desc);
