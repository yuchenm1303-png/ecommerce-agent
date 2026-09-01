begin;

create extension if not exists pgcrypto with schema extensions;

-- Commerce reuses the existing listing-monitor tenant boundary.  This bridge
-- gives the ERP domain a neutral workspace name without creating a second
-- membership or authorization system.
create table if not exists public.commerce_workspaces (
  workspace_id uuid primary key references public.listing_monitor_tenants(id) on delete cascade,
  created_at timestamptz not null default now()
);

insert into public.commerce_workspaces (workspace_id)
select id from public.listing_monitor_tenants
on conflict (workspace_id) do nothing;

create table if not exists public.commerce_products (
  workspace_id uuid not null references public.commerce_workspaces(workspace_id) on delete cascade,
  product_id text not null check (char_length(product_id) between 1 and 200),
  display_name text not null check (char_length(btrim(display_name)) between 1 and 500),
  product_type_en text not null default '' check (char_length(product_type_en) <= 240),
  brand text not null default '' check (char_length(brand) <= 200),
  status text not null default 'active' check (status in ('active', 'archived')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (workspace_id, product_id)
);

create table if not exists public.commerce_product_variants (
  workspace_id uuid not null references public.commerce_workspaces(workspace_id) on delete cascade,
  variant_id text not null check (char_length(variant_id) between 1 and 200),
  product_id text not null check (char_length(product_id) between 1 and 200),
  sku text not null default '' check (char_length(sku) <= 160),
  option_values jsonb not null default '{}'::jsonb check (jsonb_typeof(option_values) = 'object'),
  is_default boolean not null default false,
  created_at timestamptz not null default now(),
  primary key (workspace_id, variant_id),
  unique (workspace_id, variant_id, product_id),
  foreign key (workspace_id, product_id)
    references public.commerce_products(workspace_id, product_id) on delete cascade,
  check (
    (is_default and option_values = '{}'::jsonb)
    or (not is_default and option_values <> '{}'::jsonb)
  )
);

create unique index if not exists commerce_product_variants_one_default_uq
  on public.commerce_product_variants (workspace_id, product_id)
  where is_default;

create index if not exists commerce_product_variants_product_idx
  on public.commerce_product_variants (workspace_id, product_id);

create table if not exists public.commerce_source_products (
  workspace_id uuid not null references public.commerce_workspaces(workspace_id) on delete cascade,
  source_product_id text not null check (char_length(source_product_id) between 1 and 200),
  product_id text not null check (char_length(product_id) between 1 and 200),
  variant_id text not null check (char_length(variant_id) between 1 and 200),
  supplier_url text not null check (char_length(btrim(supplier_url)) between 1 and 4000),
  request_identity text not null check (char_length(btrim(request_identity)) between 1 and 4000),
  binding_method text not null check (
    binding_method in (
      'created_new_product',
      'same_source_identity',
      'ai_semantic_match',
      'user_confirmed',
      'imported'
    )
  ),
  source_system text not null default '' check (char_length(source_system) <= 120),
  external_product_id text not null default '' check (char_length(external_product_id) <= 240),
  created_at timestamptz not null default now(),
  primary key (workspace_id, source_product_id),
  foreign key (workspace_id, variant_id, product_id)
    references public.commerce_product_variants(workspace_id, variant_id, product_id)
    on delete restrict
);

-- request_identity can be several KB long, so dedupe on a fixed-size digest
-- instead of building a large text btree key.  The application still performs
-- exact identity comparison after lookup; this index is only the uniqueness
-- boundary for a deterministic supplier request identity.
create unique index if not exists commerce_source_products_request_identity_uq
  on public.commerce_source_products (
    workspace_id,
    extensions.digest(request_identity, 'sha256')
  );

create unique index if not exists commerce_source_products_external_id_uq
  on public.commerce_source_products (workspace_id, source_system, external_product_id)
  where btrim(source_system) <> '' and btrim(external_product_id) <> '';

create table if not exists public.commerce_channel_accounts (
  workspace_id uuid not null references public.commerce_workspaces(workspace_id) on delete cascade,
  channel_account_id text not null check (char_length(channel_account_id) between 1 and 200),
  channel text not null check (
    channel ~ '^[a-z0-9][a-z0-9_-]{0,31}$'
  ),
  label text not null check (char_length(btrim(label)) between 1 and 120),
  status text not null default 'active' check (status in ('active', 'archived')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (workspace_id, channel_account_id),
  unique (workspace_id, channel_account_id, channel)
);

comment on table public.commerce_channel_accounts is
  'Marketplace account identity only; browser profiles and credentials remain outside Commerce persistence.';

create table if not exists public.commerce_channel_listings (
  workspace_id uuid not null references public.commerce_workspaces(workspace_id) on delete cascade,
  listing_id text not null check (char_length(listing_id) between 1 and 200),
  product_id text not null check (char_length(product_id) between 1 and 200),
  variant_id text not null check (char_length(variant_id) between 1 and 200),
  channel text not null check (
    channel ~ '^[a-z0-9][a-z0-9_-]{0,31}$'
  ),
  channel_account_id text not null check (char_length(channel_account_id) between 1 and 200),
  external_listing_id text not null default '' check (char_length(external_listing_id) <= 500),
  status text not null default 'draft' check (status in ('draft', 'active', 'inactive', 'removed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (workspace_id, listing_id),
  foreign key (workspace_id, variant_id, product_id)
    references public.commerce_product_variants(workspace_id, variant_id, product_id)
    on delete restrict,
  foreign key (workspace_id, channel_account_id, channel)
    references public.commerce_channel_accounts(workspace_id, channel_account_id, channel)
    on delete restrict,
  check (status <> 'active' or btrim(external_listing_id) <> '')
);

create unique index if not exists commerce_channel_listings_external_id_uq
  on public.commerce_channel_listings (
    workspace_id,
    channel,
    channel_account_id,
    external_listing_id
  )
  where btrim(external_listing_id) <> '';

create index if not exists commerce_channel_listings_variant_account_idx
  on public.commerce_channel_listings (workspace_id, variant_id, channel_account_id);

create or replace function public.set_commerce_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists commerce_products_touch_updated_at on public.commerce_products;
create trigger commerce_products_touch_updated_at
before update on public.commerce_products
for each row execute function public.set_commerce_updated_at();

drop trigger if exists commerce_channel_accounts_touch_updated_at on public.commerce_channel_accounts;
create trigger commerce_channel_accounts_touch_updated_at
before update on public.commerce_channel_accounts
for each row execute function public.set_commerce_updated_at();

drop trigger if exists commerce_channel_listings_touch_updated_at on public.commerce_channel_listings;
create trigger commerce_channel_listings_touch_updated_at
before update on public.commerce_channel_listings
for each row execute function public.set_commerce_updated_at();

-- Commerce is server-gateway-only in v1.  The packaged desktop never receives a
-- service-role key.  A trusted Edge Function must authenticate the caller and
-- resolve membership through the existing listing-monitor tenant scope before
-- using these tables.
alter table public.commerce_workspaces enable row level security;
alter table public.commerce_products enable row level security;
alter table public.commerce_product_variants enable row level security;
alter table public.commerce_source_products enable row level security;
alter table public.commerce_channel_accounts enable row level security;
alter table public.commerce_channel_listings enable row level security;

revoke all on table public.commerce_workspaces from anon, authenticated;
revoke all on table public.commerce_products from anon, authenticated;
revoke all on table public.commerce_product_variants from anon, authenticated;
revoke all on table public.commerce_source_products from anon, authenticated;
revoke all on table public.commerce_channel_accounts from anon, authenticated;
revoke all on table public.commerce_channel_listings from anon, authenticated;

grant select, insert, update, delete on table public.commerce_workspaces to service_role;
grant select, insert, update, delete on table public.commerce_products to service_role;
grant select, insert, update, delete on table public.commerce_product_variants to service_role;
grant select, insert, update, delete on table public.commerce_source_products to service_role;
grant select, insert, update, delete on table public.commerce_channel_accounts to service_role;
grant select, insert, update, delete on table public.commerce_channel_listings to service_role;

drop policy if exists commerce_workspaces_deny_direct on public.commerce_workspaces;
create policy commerce_workspaces_deny_direct on public.commerce_workspaces
for all to anon, authenticated using (false) with check (false);

drop policy if exists commerce_products_deny_direct on public.commerce_products;
create policy commerce_products_deny_direct on public.commerce_products
for all to anon, authenticated using (false) with check (false);

drop policy if exists commerce_product_variants_deny_direct on public.commerce_product_variants;
create policy commerce_product_variants_deny_direct on public.commerce_product_variants
for all to anon, authenticated using (false) with check (false);

drop policy if exists commerce_source_products_deny_direct on public.commerce_source_products;
create policy commerce_source_products_deny_direct on public.commerce_source_products
for all to anon, authenticated using (false) with check (false);

drop policy if exists commerce_channel_accounts_deny_direct on public.commerce_channel_accounts;
create policy commerce_channel_accounts_deny_direct on public.commerce_channel_accounts
for all to anon, authenticated using (false) with check (false);

drop policy if exists commerce_channel_listings_deny_direct on public.commerce_channel_listings;
create policy commerce_channel_listings_deny_direct on public.commerce_channel_listings
for all to anon, authenticated using (false) with check (false);

commit;
