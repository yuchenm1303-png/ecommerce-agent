-- Immutable execution revisions, independent of the latest task snapshot.
-- Never drop or rewrite listing_task_audits or existing task log chunks.
create table if not exists public.listing_task_audit_revisions (
  id bigint generated always as identity primary key,
  audit_id uuid not null,
  user_id uuid not null,
  session_id uuid,
  device_id text not null,
  app_version text,
  task_kind text not null,
  batch_id text,
  job_id text,
  phase text not null,
  status text not null,
  product_url text,
  error_text text,
  review_required boolean,
  review_reason text,
  input_data jsonb not null default '{}'::jsonb,
  result_data jsonb not null default '{}'::jsonb,
  task_started_at timestamptz,
  task_completed_at timestamptz,
  source_updated_at timestamptz,
  snapshot_origin text not null default 'live_write',
  recorded_at timestamptz not null default clock_timestamp()
);
create index if not exists listing_task_audit_revisions_user_time_idx
  on public.listing_task_audit_revisions (user_id, recorded_at desc, id desc);
create index if not exists listing_task_audit_revisions_audit_idx
  on public.listing_task_audit_revisions (audit_id, id desc);

alter table public.listing_task_audit_revisions enable row level security;
revoke all on table public.listing_task_audit_revisions from anon, authenticated;
grant select, insert on table public.listing_task_audit_revisions to service_role;
grant usage, select on sequence public.listing_task_audit_revisions_id_seq to service_role;

create or replace function public.record_listing_task_audit_revision()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- A transport retry of identical data is not a new execution revision.
  if tg_op = 'UPDATE' and
     row(new.phase, new.status, new.product_url, new.input_data,
         new.result_data, new.error_text, new.started_at, new.completed_at,
         new.review_required, new.review_reason)
     is not distinct from
     row(old.phase, old.status, old.product_url, old.input_data,
         old.result_data, old.error_text, old.started_at, old.completed_at,
         old.review_required, old.review_reason)
  then return new; end if;

  insert into public.listing_task_audit_revisions (
    audit_id,user_id,session_id,device_id,app_version,task_kind,
    batch_id,job_id,phase,status,product_url,error_text,review_required,
    review_reason,input_data,result_data,task_started_at,task_completed_at,
    source_updated_at
  ) values (
    new.id,new.user_id,new.session_id,new.device_id,new.app_version,new.task_kind,
    new.batch_id,new.job_id,new.phase,new.status,new.product_url,new.error_text,
    new.review_required,new.review_reason,new.input_data,new.result_data,
    new.started_at,new.completed_at,new.updated_at
  );
  return new;
end;
$$;
revoke all on function public.record_listing_task_audit_revision() from public;
drop trigger if exists listing_task_audit_revision_on_write on public.listing_task_audits;
create trigger listing_task_audit_revision_on_write
after insert or update on public.listing_task_audits
for each row execute function public.record_listing_task_audit_revision();

-- Anchor the existing current state without fabricating historical attempts.
insert into public.listing_task_audit_revisions (
  audit_id,user_id,session_id,device_id,app_version,task_kind,batch_id,job_id,
  phase,status,product_url,error_text,review_required,review_reason,input_data,
  result_data,task_started_at,task_completed_at,source_updated_at,snapshot_origin,recorded_at
)
select a.id,a.user_id,a.session_id,a.device_id,a.app_version,a.task_kind,
 a.batch_id,a.job_id,a.phase,a.status,a.product_url,a.error_text,a.review_required,
 a.review_reason,a.input_data,a.result_data,a.started_at,a.completed_at,
 a.updated_at,'baseline_import',clock_timestamp()
from public.listing_task_audits a
where not exists (
  select 1 from public.listing_task_audit_revisions r where r.audit_id = a.id
);
comment on table public.listing_task_audit_revisions is
  'Append-only snapshots on meaningful audit writes; recorded_at is ingestion time, not original execution time.';
