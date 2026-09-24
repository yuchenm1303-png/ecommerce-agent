alter table public.listing_task_audits
  add column if not exists review_required boolean not null default false,
  add column if not exists review_reason text not null default '';

comment on column public.listing_task_audits.review_required is
  'Orthogonal review flag. A completed task may still require human review.';
comment on column public.listing_task_audits.review_reason is
  'Human-review reason kept separate from task failure/error state.';

create or replace function private.normalize_listing_task_audit_review_semantics()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_phase text := lower(coalesce(new.phase, ''));
  v_status text := lower(coalesce(new.status, ''));
  v_result jsonb := coalesce(new.result_data, '{}'::jsonb);
  v_job_status text := upper(coalesce(v_result->>'job_status', ''));
  v_payload_review boolean := lower(coalesce(v_result->>'review_required', 'false')) in ('true', '1', 'yes');
  v_execution boolean := v_phase like '%\_execute' escape '\';
  v_hard_failure boolean := v_status in ('failed', 'cancelled') or v_job_status in ('FAILED', 'STOPPED');
  v_review boolean;
  v_reason text;
begin
  if v_hard_failure then
    new.review_required := false;
    new.review_reason := '';
    return new;
  end if;

  v_review := coalesce(new.review_required, false)
    or v_payload_review
    or v_status = 'review'
    or v_job_status = 'REVIEW';

  v_reason := case when v_review then coalesce(
    nullif(btrim(coalesce(v_result->>'review_reason', '')), ''),
    nullif(btrim(coalesce(v_result->>'error', '')), ''),
    nullif(btrim(coalesce(new.review_reason, '')), ''),
    nullif(btrim(coalesce(new.error_text, '')), ''),
    ''
  ) else '' end;

  new.review_required := v_review;
  new.review_reason := v_reason;

  if v_execution and v_review then
    new.status := 'completed';
    new.error_text := '';
    new.result_data := (v_result - 'failure_diagnostic') || jsonb_build_object(
      'review_required', true,
      'review_reason', v_reason,
      'error', ''
    );
  end if;

  return new;
end;
$$;

revoke all on function private.normalize_listing_task_audit_review_semantics() from public, anon, authenticated;

DROP TRIGGER IF EXISTS listing_task_audits_review_semantics_trg ON public.listing_task_audits;
create trigger listing_task_audits_review_semantics_trg
before insert or update of phase, status, result_data, error_text, review_required, review_reason
on public.listing_task_audits
for each row execute function private.normalize_listing_task_audit_review_semantics();

-- Re-project existing native audits through the same database boundary. This is
-- intentionally an UPDATE rather than a one-off CASE patch so historical rows
-- and all future writes obey one invariant.
update public.listing_task_audits
set result_data = result_data
where lower(coalesce(status, '')) = 'review'
   or lower(coalesce(result_data->>'review_required', 'false')) in ('true', '1', 'yes')
   or upper(coalesce(result_data->>'job_status', '')) = 'REVIEW';

create or replace function private.listing_usage_logical_tasks(
  p_from timestamptz,
  p_to timestamptz
)
returns table (
  user_id uuid,
  task_kind text,
  status text,
  event_at timestamptz,
  logical_key text
)
language sql
stable
security invoker
set search_path = ''
as $$
  with base as (
    select a.*
    from public.listing_task_audits a
    where coalesce(a.completed_at, a.updated_at, a.created_at) >= p_from
      and coalesce(a.completed_at, a.updated_at, a.created_at) < p_to
  ),
  native_rows as (
    select
      a.user_id,
      a.task_kind,
      a.status,
      coalesce(a.completed_at, a.updated_at, a.created_at) as event_at,
      case
        when a.task_kind = 'batch' then concat_ws('|',
          'batch',
          a.user_id::text,
          coalesce(
            nullif(a.input_data->>'batch_id', ''),
            nullif(a.result_data->>'batch_id', ''),
            to_char(a.started_at at time zone 'UTC', 'YYYYMMDDHH24MISS')
          ),
          coalesce(
            nullif(a.input_data->>'job_id', ''),
            nullif(a.result_data->>'job_id', ''),
            md5(coalesce(a.product_url, ''))
          )
        )
        else concat_ws('|',
          'single',
          a.user_id::text,
          coalesce(
            nullif(a.result_data->>'run_id', ''),
            nullif(a.input_data->>'run_id', ''),
            a.id::text
          )
        )
      end as logical_key,
      a.updated_at,
      a.created_at
    from base a
    where a.task_kind = 'single'
       or (
         a.task_kind = 'batch'
         and (
           coalesce(a.input_data->>'audit_scope', '') = 'batch_link'
           or coalesce(a.result_data->>'audit_scope', '') = 'batch_link'
           or (
             coalesce(a.input_data->>'job_id', '') <> ''
             and coalesce(a.product_url, '') <> ''
           )
         )
       )
  ),
  legacy_base as (
    select
      a.*,
      greatest(
        case when jsonb_typeof(a.input_data->'items') = 'array' then jsonb_array_length(a.input_data->'items') else 0 end,
        case when jsonb_typeof(a.result_data->'jobs') = 'array' then jsonb_array_length(a.result_data->'jobs') else 0 end,
        case when coalesce(a.input_data->>'item_count', '') ~ '^\d+$' then (a.input_data->>'item_count')::integer else 0 end,
        case when coalesce(a.result_data->>'job_count', '') ~ '^\d+$' then (a.result_data->>'job_count')::integer else 0 end
      ) as child_count
    from base a
    where a.task_kind = 'batch'
      and not (
        coalesce(a.input_data->>'audit_scope', '') = 'batch_link'
        or coalesce(a.result_data->>'audit_scope', '') = 'batch_link'
        or (
          coalesce(a.input_data->>'job_id', '') <> ''
          and coalesce(a.product_url, '') <> ''
        )
      )
  ),
  legacy_rows as (
    select
      a.user_id,
      'batch'::text as task_kind,
      case
        when lower(a.phase) = 'batch_execute' then
          case upper(coalesce(job.value->>'status', ''))
            when 'DONE' then 'completed'
            when 'REVIEW' then 'completed'
            when 'FAILED' then 'failed'
            when 'STOPPED' then 'cancelled'
            else a.status
          end
        else
          case upper(coalesce(job.value->>'status', ''))
            when 'READY' then 'ready'
            when 'REVIEW' then 'review'
            when 'FAILED' then 'failed'
            when 'STOPPED' then 'cancelled'
            else a.status
          end
      end as status,
      coalesce(
        nullif(job.value->>'updated_at', '')::timestamptz,
        a.completed_at,
        a.updated_at,
        a.created_at
      ) as event_at,
      concat_ws('|',
        'legacy-batch',
        a.user_id::text,
        a.id::text,
        coalesce(nullif(job.value->>'job_id', ''), 'JOB-' || lpad((g.idx + 1)::text, 3, '0'))
      ) as logical_key,
      a.updated_at,
      a.created_at
    from legacy_base a
    cross join lateral generate_series(0, greatest(a.child_count - 1, 0)) as g(idx)
    left join lateral (
      select coalesce(a.result_data->'jobs'->g.idx, '{}'::jsonb) as value
    ) job on true
    where a.child_count > 0
  ),
  logical_raw as (
    select * from native_rows
    union all
    select * from legacy_rows
  ),
  ranked as (
    select
      r.*,
      row_number() over (
        partition by r.logical_key
        order by r.updated_at desc nulls last, r.created_at desc nulls last
      ) as rn
    from logical_raw r
  )
  select r.user_id, r.task_kind, r.status, r.event_at, r.logical_key
  from ranked r
  where r.rn = 1
    and r.event_at >= p_from
    and r.event_at < p_to;
$$;

revoke all on function private.listing_usage_logical_tasks(timestamptz, timestamptz) from public, anon, authenticated;
grant execute on function private.listing_usage_logical_tasks(timestamptz, timestamptz) to service_role;

-- The faulty legacy projection was introduced with the persistent read model on
-- 2026-08-20. Rebuild exactly that affected monitoring window so existing cards,
-- success counts and heatmap data converge immediately after migration.
select public.refresh_listing_usage_read_model(
  '2026-08-20 00:00:00+00'::timestamptz,
  now() + interval '1 minute'
);