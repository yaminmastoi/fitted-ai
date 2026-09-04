begin;

insert into storage.buckets(
  id,
  name,
  public,
  file_size_limit,
  allowed_mime_types
)
values(
  'tryon-private',
  'tryon-private',
  false,
  10485760,
  array['image/jpeg','image/png','image/webp']
)
on conflict(id) do update
set
  public = false,
  file_size_limit = 10485760,
  allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "deny direct tryon reads" on storage.objects;

create policy "deny direct tryon reads"
on storage.objects
for select
to anon, authenticated
using (false);

commit;