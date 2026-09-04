begin;

-- Sync all existing users first.
update public.profiles p
set
  email = u.email,
  email_verified_at = u.email_confirmed_at,
  updated_at = now()
from auth.users u
where p.id = u.id
  and (
    p.email is distinct from u.email
    or p.email_verified_at is distinct from u.email_confirmed_at
  );


-- Keep profile email + verification state synced in future.
create or replace function public.sync_auth_user_profile()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.profiles
  set
    email = new.email,
    email_verified_at = new.email_confirmed_at,
    updated_at = now()
  where id = new.id;

  return new;
end;
$$;


drop trigger if exists auth_user_profile_updated on auth.users;

create trigger auth_user_profile_updated
after update of email, email_confirmed_at
on auth.users
for each row
execute function public.sync_auth_user_profile();


-- Fix ensure_profile:
-- never blindly mark an account verified with now().
create or replace function public.ensure_profile(
  p_id uuid,
  p_email text
)
returns setof public.profiles
language plpgsql
security definer
set search_path = public
as $$
declare
  v_confirmed_at timestamptz;
begin

  select email_confirmed_at
  into v_confirmed_at
  from auth.users
  where id = p_id;

  insert into public.profiles(
    id,
    email,
    email_verified_at
  )
  values(
    p_id,
    p_email,
    v_confirmed_at
  )
  on conflict(id) do update
  set
    email = excluded.email,
    email_verified_at = excluded.email_verified_at,
    last_seen_at = now(),
    updated_at = now();

  insert into public.credit_wallets(user_id)
  values(p_id)
  on conflict do nothing;

  return query
  select *
  from public.profiles
  where id = p_id;

end;
$$;

commit;