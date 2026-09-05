insert into storage.buckets (id, name, public, file_size_limit)
values ('listing-studio-downloads', 'listing-studio-downloads', false, 52428800)
on conflict (id) do update
set public = false,
    file_size_limit = 52428800;

-- Legacy release storage is no longer part of the distribution architecture.
-- Keep any existing objects intact, but never expose that bucket publicly.
update storage.buckets
set public = false
where id = 'listing-studio-releases';
