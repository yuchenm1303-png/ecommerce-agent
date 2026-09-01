begin;

create table if not exists public.commerce_sync_events (
  workspace_id uuid not null references public.commerce_workspaces(workspace_id) on delete cascade,
  event_id text not null check (char_length(btrim(event_id)) between 1 and 200),
  event_kind text not null check (event_kind in ('listing_success')),
  request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
  result jsonb not null default '{}'::jsonb check (jsonb_typeof(result) = 'object'),
  occurred_at timestamptz not null,
  accepted_at timestamptz not null default now(),
  primary key (workspace_id, event_id)
);

comment on table public.commerce_sync_events is
  'Idempotency ledger for trusted Commerce gateway writes. Desktop credentials are never stored here.';

alter table public.commerce_sync_events enable row level security;
revoke all on table public.commerce_sync_events from anon, authenticated;
grant select, insert, update, delete on table public.commerce_sync_events to service_role;

drop policy if exists commerce_sync_events_deny_direct on public.commerce_sync_events;
create policy commerce_sync_events_deny_direct on public.commerce_sync_events
for all to anon, authenticated using (false) with check (false);

create or replace function public.commerce_sync_listing_success_v1(
  p_workspace_id uuid,
  p_event_id text,
  p_occurred_at timestamptz,
  p_payload jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
  v_product jsonb := coalesce(p_payload -> 'product', '{}'::jsonb);
  v_source jsonb := coalesce(p_payload -> 'source', '{}'::jsonb);
  v_account jsonb := coalesce(p_payload -> 'channel_account', '{}'::jsonb);
  v_listing jsonb := coalesce(p_payload -> 'listing', '{}'::jsonb);
  v_product_id text := btrim(coalesce(v_product ->> 'product_id', ''));
  v_variant_id text := btrim(coalesce(v_product ->> 'variant_id', ''));
  v_source_product_id text := btrim(coalesce(v_source ->> 'source_product_id', ''));
  v_request_identity text := btrim(coalesce(v_source ->> 'request_identity', ''));
  v_supplier_url text := btrim(coalesce(v_source ->> 'supplier_url', ''));
  v_channel text := lower(btrim(coalesce(v_account ->> 'channel', '')));
  v_channel_account_id text := btrim(coalesce(v_account ->> 'channel_account_id', ''));
  v_listing_id text := btrim(coalesce(v_listing ->> 'listing_id', ''));
  v_external_listing_id text := btrim(coalesce(v_listing ->> 'external_listing_id', ''));
  v_hash text;
  v_existing_hash text;
  v_existing_result jsonb;
  v_existing_product_id text;
  v_existing_variant_id text;
  v_existing_listing_id text;
  v_existing_listing_product_id text;
  v_existing_listing_variant_id text;
  v_result jsonb;
begin
  if p_workspace_id is null then
    return jsonb_build_object('accepted', false, 'error', 'workspace_required');
  end if;
  if btrim(coalesce(p_event_id, '')) = '' or char_length(p_event_id) > 200 then
    return jsonb_build_object('accepted', false, 'error', 'invalid_event_id');
  end if;
  if p_occurred_at is null then
    return jsonb_build_object('accepted', false, 'error', 'occurred_at_required');
  end if;
  if jsonb_typeof(coalesce(p_payload, 'null'::jsonb)) <> 'object' then
    return jsonb_build_object('accepted', false, 'error', 'invalid_payload');
  end if;
  if v_product_id = '' or v_variant_id = '' or v_source_product_id = '' then
    return jsonb_build_object('accepted', false, 'error', 'product_identity_required');
  end if;
  if v_request_identity = '' or v_supplier_url = '' then
    return jsonb_build_object('accepted', false, 'error', 'source_identity_required');
  end if;
  if v_channel = '' or v_channel_account_id = '' then
    return jsonb_build_object('accepted', false, 'error', 'channel_account_required');
  end if;
  if v_listing_id = '' or v_external_listing_id = '' then
    return jsonb_build_object('accepted', false, 'error', 'external_listing_id_required');
  end if;

  if not exists (
    select 1 from public.listing_monitor_tenants where id = p_workspace_id
  ) then
    return jsonb_build_object('accepted', false, 'error', 'workspace_not_found');
  end if;

  insert into public.commerce_workspaces (workspace_id)
  values (p_workspace_id)
  on conflict (workspace_id) do nothing;

  -- Serialize retries and concurrent observations of the same supplier/listing
  -- identity inside this database transaction. No desktop lock is trusted.
  perform pg_advisory_xact_lock(
    hashtextextended(p_workspace_id::text || ':event:' || p_event_id, 0)
  );
  perform pg_advisory_xact_lock(
    hashtextextended(p_workspace_id::text || ':source:' || v_request_identity, 0)
  );
  perform pg_advisory_xact_lock(
    hashtextextended(
      p_workspace_id::text || ':listing:' || v_channel || ':' ||
      v_channel_account_id || ':' || v_external_listing_id,
      0
    )
  );

  v_hash := encode(extensions.digest(convert_to(p_payload::text, 'UTF8'), 'sha256'), 'hex');
  select request_hash, result
    into v_existing_hash, v_existing_result
  from public.commerce_sync_events
  where workspace_id = p_workspace_id and event_id = p_event_id;
  if found then
    if v_existing_hash <> v_hash then
      return jsonb_build_object('accepted', false, 'error', 'event_payload_conflict');
    end if;
    return coalesce(v_existing_result, '{}'::jsonb) || jsonb_build_object(
      'accepted', true,
      'idempotent', true,
      'event_id', p_event_id
    );
  end if;

  -- Exact source identity is the only automatic Product reuse rule here. The DB
  -- never title-matches, fuzzy-matches or otherwise guesses product semantics.
  select product_id, variant_id
    into v_existing_product_id, v_existing_variant_id
  from public.commerce_source_products
  where workspace_id = p_workspace_id
    and extensions.digest(request_identity, 'sha256') = extensions.digest(v_request_identity, 'sha256')
    and request_identity = v_request_identity
  limit 1;

  if found then
    v_product_id := v_existing_product_id;
    v_variant_id := v_existing_variant_id;
  else
    insert into public.commerce_products (
      workspace_id, product_id, display_name, product_type_en, brand, status
    ) values (
      p_workspace_id,
      v_product_id,
      btrim(coalesce(v_product ->> 'display_name', '')),
      btrim(coalesce(v_product ->> 'product_type_en', '')),
      btrim(coalesce(v_product ->> 'brand', '')),
      'active'
    )
    on conflict (workspace_id, product_id) do update set
      display_name = excluded.display_name,
      product_type_en = excluded.product_type_en,
      brand = excluded.brand,
      status = 'active';

    insert into public.commerce_product_variants (
      workspace_id, variant_id, product_id, sku, option_values, is_default
    ) values (
      p_workspace_id,
      v_variant_id,
      v_product_id,
      btrim(coalesce(v_product ->> 'sku', '')),
      '{}'::jsonb,
      true
    )
    on conflict (workspace_id, variant_id) do nothing;

    insert into public.commerce_source_products (
      workspace_id,
      source_product_id,
      product_id,
      variant_id,
      supplier_url,
      request_identity,
      binding_method,
      source_system,
      external_product_id
    ) values (
      p_workspace_id,
      v_source_product_id,
      v_product_id,
      v_variant_id,
      v_supplier_url,
      v_request_identity,
      'created_new_product',
      btrim(coalesce(v_source ->> 'source_system', '')),
      btrim(coalesce(v_source ->> 'external_product_id', ''))
    );
  end if;

  insert into public.commerce_channel_accounts (
    workspace_id, channel_account_id, channel, label, status
  ) values (
    p_workspace_id,
    v_channel_account_id,
    v_channel,
    btrim(coalesce(v_account ->> 'label', '')),
    'active'
  )
  on conflict (workspace_id, channel_account_id) do update set
    channel = excluded.channel,
    label = excluded.label,
    status = 'active';

  select listing_id, product_id, variant_id
    into v_existing_listing_id, v_existing_listing_product_id, v_existing_listing_variant_id
  from public.commerce_channel_listings
  where workspace_id = p_workspace_id
    and channel = v_channel
    and channel_account_id = v_channel_account_id
    and external_listing_id = v_external_listing_id
  limit 1;

  if found then
    if v_existing_listing_product_id <> v_product_id
       or v_existing_listing_variant_id <> v_variant_id then
      return jsonb_build_object('accepted', false, 'error', 'listing_product_conflict');
    end if;
    v_listing_id := v_existing_listing_id;
  end if;

  insert into public.commerce_channel_listings (
    workspace_id,
    listing_id,
    product_id,
    variant_id,
    channel,
    channel_account_id,
    external_listing_id,
    status
  ) values (
    p_workspace_id,
    v_listing_id,
    v_product_id,
    v_variant_id,
    v_channel,
    v_channel_account_id,
    v_external_listing_id,
    'active'
  )
  on conflict (workspace_id, listing_id) do update set
    external_listing_id = excluded.external_listing_id,
    status = 'active';

  v_result := jsonb_build_object(
    'accepted', true,
    'idempotent', false,
    'event_id', p_event_id,
    'workspace_id', p_workspace_id,
    'product_id', v_product_id,
    'variant_id', v_variant_id,
    'channel_account_id', v_channel_account_id,
    'listing_id', v_listing_id,
    'external_listing_id', v_external_listing_id
  );

  insert into public.commerce_sync_events (
    workspace_id, event_id, event_kind, request_hash, result, occurred_at
  ) values (
    p_workspace_id, p_event_id, 'listing_success', v_hash, v_result, p_occurred_at
  );

  return v_result;
end;
$$;

revoke all on function public.commerce_sync_listing_success_v1(uuid, text, timestamptz, jsonb)
  from public, anon, authenticated;
grant execute on function public.commerce_sync_listing_success_v1(uuid, text, timestamptz, jsonb)
  to service_role;

commit;
