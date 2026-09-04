begin;
alter table ad_placements add column deleted_at timestamptz;
commit;
