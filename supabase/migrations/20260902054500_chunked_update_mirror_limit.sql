update storage.buckets
set
  public = true,
  file_size_limit = 52428800,
  allowed_mime_types = array['application/json', 'application/octet-stream']::text[]
where id = 'listing-studio-updates';
