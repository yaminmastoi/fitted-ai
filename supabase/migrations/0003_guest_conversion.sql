begin;
create or replace function convert_guest_to_user(p_guest uuid,p_user uuid) returns boolean language plpgsql security definer set search_path=public as $$
declare g guest_sessions%rowtype;begin
 select * into g from guest_sessions where id=p_guest for update;
 if not found then return false;end if;
 if g.converted_user_id is not null then return g.converted_user_id=p_user;end if;
 update guest_sessions set converted_user_id=p_user,status='converted',updated_at=now() where id=p_guest;
 update generations set user_id=p_user,guest_id=null where guest_id=p_guest;
 update analytics_events set user_id=p_user where guest_id=p_guest and user_id is null;
 return true;
end $$;
revoke all on function convert_guest_to_user(uuid,uuid) from public,anon,authenticated;
grant execute on function convert_guest_to_user(uuid,uuid) to service_role;
commit;
