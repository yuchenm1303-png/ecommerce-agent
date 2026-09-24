insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'listing-studio-updates',
  'listing-studio-updates',
  true,
  536870912,
  array['application/json', 'application/octet-stream']::text[]
)
on conflict (id) do update
set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;
