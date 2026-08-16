revoke execute on function public.get_listing_usage_admin_snapshot() from authenticated;
grant execute on function public.get_listing_usage_admin_snapshot() to service_role;

drop policy if exists "listing usage sessions deny direct client access" on public.listing_usage_sessions;
create policy "listing usage sessions deny direct client access"
on public.listing_usage_sessions
for all
to public
using (false)
with check (false);

drop policy if exists "listing usage events deny direct client access" on public.listing_usage_events;
create policy "listing usage events deny direct client access"
on public.listing_usage_events
for all
to public
using (false)
with check (false);
