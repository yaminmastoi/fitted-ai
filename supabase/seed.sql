-- Optional local development ad. It is disabled by default and produces no fabricated revenue.
insert into ad_placements(name,slug,provider,ad_unit_id,location,is_active,priority,max_per_session)
values('Try-on lower slot','tryon_bottom','your-ad-provider','replace-me','tryon_bottom',false,10,1)
on conflict(slug) do nothing;
