begin;
create extension if not exists pgcrypto;

create type account_status as enum ('active','restricted','suspended','deleted');
create type app_role as enum ('user','support_admin','admin','super_admin');
create type generation_status as enum ('queued','processing','completed','failed','cancelled','expired');
create type payment_status as enum ('pending','paid','failed','cancelled','refunded','partially_refunded');
create type credit_transaction_type as enum ('purchase','generation_reserve','generation_capture','generation_refund','admin_adjustment','promotion','bonus');
create type financial_event_type as enum ('package_sale','refund','provider_ai_cost','payment_fee','advertising_revenue','manual_operating_expense');

create table profiles(
 id uuid primary key references auth.users(id) on delete restrict,email text,display_name text,avatar_path text,account_status account_status not null default 'active',role app_role not null default 'user',country_code char(2),preferred_currency char(3) not null default 'USD',preferred_locale text not null default 'en',timezone text,created_at timestamptz not null default now(),updated_at timestamptz not null default now(),last_seen_at timestamptz,last_login_at timestamptz,email_verified_at timestamptz,first_paid_at timestamptz,lifetime_value numeric(14,2) not null default 0,is_paid boolean not null default false,risk_level text not null default 'low',suspended_at timestamptz,suspension_reason text
);
create table guest_sessions(
 id uuid primary key default gen_random_uuid(),public_guest_token_hash text unique not null,created_at timestamptz not null default now(),updated_at timestamptz not null default now(),last_seen_at timestamptz not null default now(),expires_at timestamptz not null,free_generations_allowed integer not null check(free_generations_allowed>=0),free_generations_used integer not null default 0 check(free_generations_used>=0),status text not null default 'active',converted_user_id uuid references profiles(id),risk_score integer not null default 0 check(risk_score between 0 and 100),risk_level text not null default 'low',abuse_status text not null default 'ALLOW',ip_hash text,ip_country char(2),device_hash text,browser_family text,os_family text,device_type text,timezone text,locale text,referrer_domain text,utm_source text,utm_medium text,utm_campaign text,constraint guest_allowance_valid check(free_generations_used<=free_generations_allowed)
);
create index guest_sessions_ip_created_idx on guest_sessions(ip_hash,created_at desc);
create index guest_sessions_device_created_idx on guest_sessions(device_hash,created_at desc) where device_hash is not null;
create unique index guest_one_conversion_idx on guest_sessions(id,converted_user_id) where converted_user_id is not null;
create table user_sessions(
 id uuid primary key default gen_random_uuid(),user_id uuid not null references profiles(id),session_started_at timestamptz not null default now(),last_seen_at timestamptz not null default now(),session_ended_at timestamptz,ip_hash text,ip_country char(2),device_type text,browser_family text,os_family text,user_agent_summary text,login_method text,risk_score integer not null default 0,is_suspicious boolean not null default false
);

create table generations(
 id uuid primary key default gen_random_uuid(),guest_id uuid references guest_sessions(id),user_id uuid references profiles(id),person_image_path text not null,garment_image_path text not null,result_image_path text,person_image_hash text not null,garment_image_hash text not null,status generation_status not null default 'queued',provider text not null default 'fal.ai' check(provider='fal.ai'),provider_job_id text,credit_source text not null,reserved_credit_transaction_id uuid,estimated_provider_cost numeric(12,5) not null default 0,actual_provider_cost numeric(12,5),provider_request_started_at timestamptz,provider_response_received_at timestamptz,error_code text,safe_error_message text,internal_error_detail text,created_at timestamptz not null default now(),queued_at timestamptz,started_at timestamptz,completed_at timestamptz,expired_at timestamptz,request_id text not null,idempotency_key text not null,constraint exactly_one_generation_owner check((guest_id is null)<>(user_id is null))
);
create unique index generations_guest_idempotency_idx on generations(guest_id,idempotency_key) where guest_id is not null;
create unique index generations_user_idempotency_idx on generations(user_id,idempotency_key) where user_id is not null;
create unique index generations_provider_job_idx on generations(provider,provider_job_id) where provider_job_id is not null;
create index generations_status_queued_idx on generations(status,queued_at) where status in ('queued','processing');

create table credit_wallets(user_id uuid primary key references profiles(id),paid_credits integer not null default 0 check(paid_credits>=0),bonus_credits integer not null default 0 check(bonus_credits>=0),reserved_credits integer not null default 0 check(reserved_credits>=0),updated_at timestamptz not null default now(),constraint reservation_not_overdrawn check(reserved_credits<=paid_credits+bonus_credits));
create table credit_packages(id uuid primary key default gen_random_uuid(),name text not null,slug text unique not null,generation_count integer not null check(generation_count>0),price numeric(12,2) not null check(price>=0),currency char(3) not null,original_price numeric(12,2),discount_percent numeric(5,2) not null default 0 check(discount_percent between 0 and 100),badge text,description text,is_active boolean not null default true,is_featured boolean not null default false,sort_order integer not null default 0,starts_at timestamptz,ends_at timestamptz,created_at timestamptz not null default now(),updated_at timestamptz not null default now(),check(ends_at is null or starts_at is null or ends_at>starts_at));
create table promotions(id uuid primary key default gen_random_uuid(),name text not null,code text unique,discount_percent numeric(5,2) not null check(discount_percent between 0 and 100),is_active boolean not null default true,starts_at timestamptz,ends_at timestamptz,usage_limit integer,usage_count integer not null default 0,created_at timestamptz not null default now(),updated_at timestamptz not null default now());
create table payments(id uuid primary key default gen_random_uuid(),user_id uuid not null references profiles(id),package_id uuid references credit_packages(id) on delete set null,amount numeric(12,2) not null,currency char(3) not null,provider text not null,provider_checkout_id text,provider_payment_id text,status payment_status not null,credits_to_grant integer not null,package_name_snapshot text not null,generation_count_snapshot integer not null,price_snapshot numeric(12,2) not null,currency_snapshot char(3) not null,discount_snapshot numeric(5,2) not null default 0,created_at timestamptz not null default now(),updated_at timestamptz not null default now());
create unique index payments_provider_payment_idx on payments(provider,provider_payment_id) where provider_payment_id is not null;
create table payment_transactions(id uuid primary key default gen_random_uuid(),payment_id uuid not null references payments(id),provider_event_id text not null,provider text not null,event_type text not null,status text not null,payload_hash text not null,created_at timestamptz not null default now(),unique(provider,provider_event_id));
create table credit_transactions(id uuid primary key default gen_random_uuid(),user_id uuid not null references profiles(id),generation_id uuid references generations(id) deferrable initially deferred,payment_id uuid references payments(id),type credit_transaction_type not null,amount integer not null,balance_before integer not null,balance_after integer not null,reason text not null,created_at timestamptz not null default now(),admin_id uuid references profiles(id),metadata jsonb not null default '{}');
alter table generations add constraint generations_reserved_credit_fk foreign key(reserved_credit_transaction_id) references credit_transactions(id) deferrable initially deferred;
create table financial_events(id uuid primary key default gen_random_uuid(),event_type financial_event_type not null,amount numeric(14,5) not null,currency char(3) not null,source text not null,reference_id text,timestamp timestamptz not null default now(),created_at timestamptz not null default now(),metadata jsonb not null default '{}');

create table ad_placements(id uuid primary key default gen_random_uuid(),name text not null,slug text unique not null,provider text not null,ad_unit_id text not null,custom_channel_id text,location text not null,is_active boolean not null default false,priority integer not null default 0,frequency_mode text not null default 'session',max_per_session integer not null default 1,device_target text not null default 'all',starts_at timestamptz,ends_at timestamptz,created_at timestamptz not null default now(),updated_at timestamptz not null default now());
create table ad_events(id bigint generated always as identity primary key,placement_id uuid not null references ad_placements(id),guest_id uuid references guest_sessions(id),user_id uuid references profiles(id),event_type text not null check(event_type in ('ad_slot_requested','ad_slot_rendered','ad_slot_visible')),created_at timestamptz not null default now());
create table ad_revenue_reports(id uuid primary key default gen_random_uuid(),provider text not null,placement_id uuid references ad_placements(id),report_date date not null,impressions bigint,clicks bigint,ctr numeric(8,5),rpm numeric(12,4),estimated_earnings numeric(14,5),currency char(3) not null,source_report_id text,created_at timestamptz not null default now(),unique(provider,placement_id,report_date));
create table analytics_events(id bigint generated always as identity primary key,event_name text not null,user_id uuid references profiles(id),guest_id uuid references guest_sessions(id),session_id text,metadata jsonb not null default '{}',created_at timestamptz not null default now());
create index analytics_event_created_idx on analytics_events(event_name,created_at);
create table system_settings(key text primary key,value jsonb not null,value_type text not null default 'json',description text,category text not null default 'general',is_public boolean not null default false,updated_at timestamptz not null default now(),updated_by uuid references profiles(id));
create table security_events(id bigint generated always as identity primary key,event_type text not null,user_id uuid references profiles(id),guest_id uuid references guest_sessions(id),ip_hash text,risk_score integer,metadata jsonb not null default '{}',created_at timestamptz not null default now());
create index security_events_type_created_idx on security_events(event_type,created_at desc);
create table admin_audit_logs(id bigint generated always as identity primary key,admin_id uuid not null references profiles(id),action text not null,entity_type text not null,entity_id text,before_json jsonb,after_json jsonb,reason text not null,ip_hash text,created_at timestamptz not null default now());
create table account_requests(id uuid primary key default gen_random_uuid(),user_id uuid not null references profiles(id),type text not null check(type in ('export','delete')),status text not null default 'queued',result_path text,created_at timestamptz not null default now(),completed_at timestamptz);

insert into system_settings(key,value,value_type,description,category,is_public) values
('free_trial_generations','3','integer','Free generations granted to an eligible risk cluster','trial',true),
('free_trial_ip_window_days','30','integer','Rolling cluster evaluation window','security',false),
('free_max_upload_mb','2','number','Per-image free upload limit','uploads',true),
('paid_max_upload_mb','10','number','Per-image paid upload limit','uploads',true),
('free_min_width','256','integer','Minimum accepted width','uploads',false),('free_min_height','256','integer','Minimum accepted height','uploads',false),
('free_max_width','6000','integer','Maximum accepted width','uploads',false),('free_max_height','6000','integer','Maximum accepted height','uploads',false),
('max_image_megapixels','24','number','Decoded pixel safety ceiling','uploads',false),
('fal_estimated_cost_per_generation','0.075','number','Estimated fal.ai cost; not actual billing','finance',false),
('guest_ads_enabled','false','boolean','Allow configured ads for guests','ads',true),('paid_ads_enabled','false','boolean','Must remain false for paid users','ads',false),
('guest_upload_retention_hours','24','integer','Guest source image retention','retention',false),('guest_result_retention_hours','24','integer','Guest result retention','retention',false),
('paid_upload_retention_days','30','integer','Paid source image retention','retention',false),('paid_result_retention_days','30','integer','Paid result retention','retention',false),
('guest_trial_rate_limit','4','integer','Guest generation requests per minute','security',false),('generation_rate_limit','10','integer','Paid generation requests per minute','security',false),
('max_active_generations_guest','1','integer','Concurrent guest generations','generation',false),('max_active_generations_user','3','integer','Concurrent user generations','generation',false),
('generation_stuck_timeout_minutes','20','integer','Time before reconciliation checks','generation',false),
('maintenance_mode','false','boolean','Suspend non-admin writes','general',true),('support_email','"support@example.com"','string','Public support address','general',true),
('guest_download_enabled','true','boolean','Guest result download policy','trial',true),('free_watermark_enabled','false','boolean','Free result watermark policy','trial',true),
('login_failed_attempts','5','integer','Failed attempts before cooldown','security',false),('login_cooldown_minutes','15','integer','Login cooldown','security',false),
('signup_rate_limit','5','integer','Signups per cluster per hour','security',false),('password_reset_rate_limit','3','integer','Reset requests per email and IP per hour','security',false);

insert into credit_packages(name,slug,generation_count,price,currency,original_price,discount_percent,badge,description,is_active,is_featured,sort_order) values
('Single Look','single-look',1,0.50,'USD',null,0,null,'One AI try-on',true,false,10),
('Style Pack','style-pack',10,4.50,'USD',5.00,10,'MOST POPULAR','Ten flexible AI try-ons',true,true,20),
('Wardrobe Pack','wardrobe-pack',30,12.00,'USD',15.00,20,'BEST VALUE','Thirty flexible AI try-ons',true,false,30);

create or replace function ensure_profile(p_id uuid,p_email text) returns setof profiles language plpgsql security definer set search_path=public as $$
begin
 insert into profiles(id,email,email_verified_at) values(p_id,p_email,now()) on conflict(id) do update set email=excluded.email,last_seen_at=now();
 insert into credit_wallets(user_id) values(p_id) on conflict do nothing;
 return query select * from profiles where id=p_id;
end $$;
create or replace function handle_auth_user() returns trigger language plpgsql security definer set search_path=public as $$
begin insert into profiles(id,email,display_name,email_verified_at) values(new.id,new.email,new.raw_user_meta_data->>'display_name',new.email_confirmed_at) on conflict(id) do nothing;insert into credit_wallets(user_id) values(new.id) on conflict do nothing;return new;end $$;
create trigger auth_user_created after insert on auth.users for each row execute function handle_auth_user();

create or replace function reserve_guest_generation(p_guest uuid,p_generation uuid,p_key text) returns table(ok boolean,remaining integer) language plpgsql security definer set search_path=public as $$
declare g guest_sessions%rowtype;begin
 perform pg_advisory_xact_lock(hashtextextended(p_guest::text||':'||p_key,0));
 select * into g from guest_sessions where id=p_guest and status='active' and expires_at>now() for update;
 if not found or g.free_generations_used>=g.free_generations_allowed then return query select false,greatest(coalesce(g.free_generations_allowed,0)-coalesce(g.free_generations_used,0),0);return;end if;
 update guest_sessions set free_generations_used=free_generations_used+1,updated_at=now(),last_seen_at=now() where id=p_guest;
 return query select true,g.free_generations_allowed-g.free_generations_used-1;
end $$;
create or replace function refund_guest_generation(p_guest uuid,p_generation uuid) returns boolean language plpgsql security definer set search_path=public as $$
begin perform pg_advisory_xact_lock(hashtextextended(p_generation::text,0));if exists(select 1 from generations where id=p_generation and credit_source='free_trial' and status='failed') then update guest_sessions set free_generations_used=greatest(0,free_generations_used-1),updated_at=now() where id=p_guest;update generations set credit_source='free_trial_refunded' where id=p_generation;return true;end if;return false;end $$;

create or replace function reserve_paid_credit(p_user uuid,p_generation uuid,p_key text) returns table(ok boolean,source text,transaction_id uuid) language plpgsql security definer set search_path=public as $$
declare w credit_wallets%rowtype;tx uuid:=gen_random_uuid();before_balance int;src text;begin
 perform pg_advisory_xact_lock(hashtextextended(p_user::text||':'||p_key,0));select * into w from credit_wallets where user_id=p_user for update;
 before_balance:=coalesce(w.paid_credits,0)+coalesce(w.bonus_credits,0)-coalesce(w.reserved_credits,0);
 if before_balance<1 then return query select false,null::text,null::uuid;return;end if;
 src:=case when w.bonus_credits-w.reserved_credits>0 then 'bonus' else 'paid' end;
 update credit_wallets set reserved_credits=reserved_credits+1,updated_at=now() where user_id=p_user;
 insert into credit_transactions(id,user_id,generation_id,type,amount,balance_before,balance_after,reason,metadata) values(tx,p_user,p_generation,'generation_reserve',-1,before_balance,before_balance-1,'Generation credit reserved',jsonb_build_object('source',src));
 return query select true,src,tx;
end $$;
create or replace function capture_paid_credit(p_user uuid,p_generation uuid) returns boolean language plpgsql security definer set search_path=public as $$
declare w credit_wallets%rowtype;reserve_tx credit_transactions%rowtype;src text;before_balance int;begin
 if exists(select 1 from credit_transactions where generation_id=p_generation and type='generation_capture') then return true;end if;
 select * into w from credit_wallets where user_id=p_user for update;select * into reserve_tx from credit_transactions where generation_id=p_generation and type='generation_reserve' for update;src:=reserve_tx.metadata->>'source';
 before_balance:=w.paid_credits+w.bonus_credits-w.reserved_credits;
 if src='bonus' then update credit_wallets set bonus_credits=bonus_credits-1,reserved_credits=reserved_credits-1,updated_at=now() where user_id=p_user;else update credit_wallets set paid_credits=paid_credits-1,reserved_credits=reserved_credits-1,updated_at=now() where user_id=p_user;end if;
 insert into credit_transactions(user_id,generation_id,type,amount,balance_before,balance_after,reason,metadata) values(p_user,p_generation,'generation_capture',0,before_balance,before_balance,'Reserved credit captured',jsonb_build_object('source',src));return true;
end $$;
create or replace function refund_paid_credit(p_user uuid,p_generation uuid,p_reason text) returns boolean language plpgsql security definer set search_path=public as $$
declare w credit_wallets%rowtype;reserve_tx credit_transactions%rowtype;before_balance int;begin
 if exists(select 1 from credit_transactions where generation_id=p_generation and type='generation_refund') then return true;end if;
 select * into reserve_tx from credit_transactions where generation_id=p_generation and type='generation_reserve';if not found then return false;end if;
 select * into w from credit_wallets where user_id=p_user for update;before_balance:=w.paid_credits+w.bonus_credits-w.reserved_credits;update credit_wallets set reserved_credits=greatest(0,reserved_credits-1),updated_at=now() where user_id=p_user;
 insert into credit_transactions(user_id,generation_id,type,amount,balance_before,balance_after,reason,metadata) values(p_user,p_generation,'generation_refund',1,before_balance,before_balance+1,p_reason,reserve_tx.metadata);return true;
end $$;
create or replace function grant_purchased_credits(p_user uuid,p_payment uuid,p_amount int) returns boolean language plpgsql security definer set search_path=public as $$
declare w credit_wallets%rowtype;begin if exists(select 1 from credit_transactions where payment_id=p_payment and type='purchase') then return true;end if;select * into w from credit_wallets where user_id=p_user for update;update credit_wallets set paid_credits=paid_credits+p_amount,updated_at=now() where user_id=p_user;insert into credit_transactions(user_id,payment_id,type,amount,balance_before,balance_after,reason) values(p_user,p_payment,'purchase',p_amount,w.paid_credits+w.bonus_credits-w.reserved_credits,w.paid_credits+w.bonus_credits-w.reserved_credits+p_amount,'Verified package purchase');update profiles set is_paid=true,first_paid_at=coalesce(first_paid_at,now()) where id=p_user;return true;end $$;
create or replace function upsert_credit_package(p_id uuid,p_name text,p_slug text,p_count int,p_price numeric,p_currency text,p_original numeric,p_discount numeric,p_badge text,p_description text,p_active boolean,p_featured boolean,p_sort int) returns setof credit_packages language plpgsql security definer set search_path=public as $$
declare result_id uuid:=coalesce(p_id,gen_random_uuid());begin insert into credit_packages(id,name,slug,generation_count,price,currency,original_price,discount_percent,badge,description,is_active,is_featured,sort_order) values(result_id,p_name,p_slug,p_count,p_price,p_currency,p_original,p_discount,p_badge,p_description,p_active,p_featured,p_sort) on conflict(id) do update set name=excluded.name,slug=excluded.slug,generation_count=excluded.generation_count,price=excluded.price,currency=excluded.currency,original_price=excluded.original_price,discount_percent=excluded.discount_percent,badge=excluded.badge,description=excluded.description,is_active=excluded.is_active,is_featured=excluded.is_featured,sort_order=excluded.sort_order,updated_at=now();return query select * from credit_packages where id=result_id;end $$;

alter table profiles enable row level security;alter table guest_sessions enable row level security;alter table user_sessions enable row level security;alter table generations enable row level security;alter table credit_wallets enable row level security;alter table credit_transactions enable row level security;alter table credit_packages enable row level security;alter table payments enable row level security;alter table payment_transactions enable row level security;alter table financial_events enable row level security;alter table ad_placements enable row level security;alter table ad_events enable row level security;alter table ad_revenue_reports enable row level security;alter table analytics_events enable row level security;alter table system_settings enable row level security;alter table security_events enable row level security;alter table admin_audit_logs enable row level security;alter table account_requests enable row level security;alter table promotions enable row level security;

create policy own_profile_select on profiles for select to authenticated using(id=auth.uid());create policy own_profile_update on profiles for update to authenticated using(id=auth.uid()) with check(id=auth.uid() and role=(select role from profiles where id=auth.uid()));
create policy own_user_sessions on user_sessions for select to authenticated using(user_id=auth.uid());create policy own_generations on generations for select to authenticated using(user_id=auth.uid());create policy own_wallet on credit_wallets for select to authenticated using(user_id=auth.uid());create policy own_credit_transactions on credit_transactions for select to authenticated using(user_id=auth.uid());create policy public_active_packages on credit_packages for select to anon,authenticated using(is_active and (starts_at is null or starts_at<=now()) and (ends_at is null or ends_at>now()));create policy own_payments on payments for select to authenticated using(user_id=auth.uid());create policy public_settings on system_settings for select to anon,authenticated using(is_public);create policy own_account_requests on account_requests for select to authenticated using(user_id=auth.uid());
revoke all on all functions in schema public from public,anon,authenticated;
grant execute on function ensure_profile(uuid,text) to service_role;grant execute on function reserve_guest_generation(uuid,uuid,text) to service_role;grant execute on function refund_guest_generation(uuid,uuid) to service_role;grant execute on function reserve_paid_credit(uuid,uuid,text) to service_role;grant execute on function capture_paid_credit(uuid,uuid) to service_role;grant execute on function refund_paid_credit(uuid,uuid,text) to service_role;grant execute on function grant_purchased_credits(uuid,uuid,int) to service_role;grant execute on function upsert_credit_package(uuid,text,text,int,numeric,text,numeric,numeric,text,text,boolean,boolean,int) to service_role;
commit;

