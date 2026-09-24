-- One logical Batch product task must occupy exactly one audit row.
-- Client-generated audit UUIDs are transport identifiers only; the durable
-- identity is user + batch_id + job_id extracted from the audit payload.

alter table public.listing_task_audits
  add column batch_id text generated always as (
    case
      when task_kind = 'batch' then coalesce(
        nullif(input_data ->> 'batch_id', ''),
        nullif(result_data ->> 'batch_id', '')
      )
      else null
    end
  ) stored;

alter table public.listing_task_audits
  add column job_id text generated always as (
    case
      when task_kind = 'batch' then coalesce(
        nullif(input_data ->> 'job_id', ''),
        nullif(result_data ->> 'job_id', '')
      )
      else null
    end
  ) stored;

-- Remove historical double-writes before installing the invariant. Keep the
-- most recently updated row because it contains the latest task state/result.
with ranked as (
  select
    id,
    row_number() over (
      partition by user_id, batch_id, job_id
      order by updated_at desc, created_at desc, id desc
    ) as rn
  from public.listing_task_audits
  where task_kind = 'batch'
    and batch_id is not null
    and job_id is not null
)
delete from public.listing_task_audits audit
using ranked duplicate
where audit.id = duplicate.id
  and duplicate.rn > 1;

-- NULLs remain unrestricted for Single audits and malformed legacy Batch rows,
-- while every well-formed Batch job is physically unique at the database layer.
alter table public.listing_task_audits
  add constraint listing_task_audits_batch_logical_key
  unique (user_id, batch_id, job_id);

comment on column public.listing_task_audits.batch_id is
  'Generated Batch operation id used for durable logical task identity.';
comment on column public.listing_task_audits.job_id is
  'Generated Batch job id used for durable logical task identity.';
