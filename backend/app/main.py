import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings
from .db import connect, disconnect, get_pool, get_redis, setting_values
from .security import (
    COOKIE_NAME,
    ApiError,
    EmailPayload,
    Principal,
    hash_value,
    optional_principal,
    rate_limit,
    request_ip,
    require_user,
    unsign_guest,
    websocket_principal,
)
from .services import (
    active_ad,
    grant_verified_payment,
    owns_generation,
    serialize_generation,
    start_generation,
    validate_image,
    verify_webhook_signature,
)


def clean(value: Any) -> Any:
    if isinstance(value, (datetime,date)): return value.isoformat()
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, uuid.UUID): return str(value)
    if isinstance(value, dict): return {k:clean(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [clean(v) for v in value]
    return value


@asynccontextmanager
async def lifespan(_:FastAPI):
    await connect()
    yield
    await disconnect()


app=FastAPI(title="Fitted API",version="1.0.0",docs_url="/api/docs" if not settings().production else None,lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=settings().origins,allow_credentials=True,allow_methods=["GET","POST","PATCH","DELETE","OPTIONS"],allow_headers=["Authorization","Content-Type","Idempotency-Key","X-Device-Token","X-Timezone","X-Locale","X-Screen-Category"],expose_headers=["X-Request-ID"])


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request:Request,call_next):
        request_id=request.headers.get("x-request-id",str(uuid.uuid4()))[:80];request.state.request_id=request_id;request.state.set_guest_cookie=None
        started=time.perf_counter()
        response=await call_next(request)
        response.headers["X-Request-ID"]=request_id
        response.headers["X-Content-Type-Options"]="nosniff";response.headers["Referrer-Policy"]="strict-origin-when-cross-origin";response.headers["Permissions-Policy"]="camera=(self), microphone=(), geolocation=(), interest-cohort=()";response.headers["X-Frame-Options"]="DENY"
        cfg=settings();response.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' data: blob: https://*.supabase.co https://*.fal.media https://cdn.fashn.ai; connect-src 'self' " + " ".join(cfg.origins) + " https://*.supabase.co wss://*.supabase.co " + cfg.csp_list(cfg.csp_connect_origins) + "; script-src 'self' " + cfg.csp_list(cfg.csp_script_origins) + "; frame-src 'self' " + cfg.csp_list(cfg.csp_frame_origins) + "; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; frame-ancestors 'none'"
        if settings().production:response.headers["Strict-Transport-Security"]="max-age=63072000; includeSubDomains; preload"
        if request.state.set_guest_cookie:response.set_cookie(COOKIE_NAME,request.state.set_guest_cookie,max_age=30*86400,httponly=True,secure=settings().production,samesite="lax",path="/")
        response.headers["Server-Timing"]=f"app;dur={(time.perf_counter()-started)*1000:.1f}"
        return response
app.add_middleware(SecurityMiddleware)


@app.exception_handler(ApiError)
async def api_error(request:Request,exc:ApiError):
    detail=exc.detail if isinstance(exc.detail,dict) else {"code":"REQUEST_FAILED","message":str(exc.detail),"details":{}}
    return JSONResponse({**detail,"request_id":request.state.request_id},status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(request:Request,_:RequestValidationError):
    return JSONResponse({"code":"INVALID_REQUEST","message":"Some request fields are invalid.","request_id":request.state.request_id,"details":{}},status_code=422)


@app.exception_handler(Exception)
async def unknown_error(request:Request,_:Exception):
    return JSONResponse({"code":"INTERNAL_ERROR","message":"Something went wrong. Please try again.","request_id":request.state.request_id,"details":{}},status_code=500)


@app.get("/health")
async def health():
    db_ok=redis_ok=False
    try: db_ok=await get_pool().fetchval("select true")
    except Exception: pass
    try: redis_ok=bool(await get_redis().ping())
    except Exception: pass
    return {"status":"ok" if db_ok and redis_ok else "degraded","database":bool(db_ok),"redis":redis_ok}


PUBLIC_KEYS=["free_trial_generations","free_max_upload_mb","paid_max_upload_mb","guest_ads_enabled","guest_download_enabled","free_watermark_enabled","support_email","maintenance_mode"]
@app.get("/api/bootstrap")
async def bootstrap(request:Request):
    principal=await optional_principal(request)
    cfg=await setting_values(PUBLIC_KEYS)
    packages=await get_pool().fetch("""select id,name,slug,generation_count,price,currency,original_price,discount_percent,badge,description,is_featured from credit_packages where is_active and (starts_at is null or starts_at<=now()) and (ends_at is null or ends_at>now()) order by sort_order""")
    session={"authenticated":principal.kind=="user","is_paid":principal.is_paid,"role":principal.role,"credits":0,"email":principal.email}
    if principal.kind=="user":
        wallet=await get_pool().fetchrow("select paid_credits,bonus_credits,reserved_credits from credit_wallets where user_id=$1",principal.id)
        if wallet:session["credits"]=wallet["paid_credits"]+wallet["bonus_credits"]-wallet["reserved_credits"]
    else:
        guest=await get_pool().fetchrow("select free_generations_allowed-free_generations_used remaining from guest_sessions where id=$1",principal.id);session["guest_remaining"]=max(0,guest["remaining"]) if guest else 0
    return clean({"config":cfg,"packages":[dict(p) for p in packages],"session":session})


@app.post("/api/generations",status_code=202)
async def create_generation(request:Request,person_image:UploadFile=File(...),garment_image:UploadFile=File(...),idempotency_key:str=Form(...)):
    principal=await optional_principal(request)
    limits=await setting_values(["generation_rate_limit","guest_trial_rate_limit","upload_rate_limit"])
    generation_limit=int(limits.get("generation_rate_limit" if principal.kind=="user" else "guest_trial_rate_limit",10 if principal.kind=="user" else 4))
    await rate_limit("generation",principal.id,generation_limit,60)
    await rate_limit("upload",principal.id,int(limits.get("upload_rate_limit",8)),60)
    lock=get_redis().lock(f"lock:idempotency:{hash_value(principal.id+':'+idempotency_key,'generation')}",timeout=90,blocking_timeout=15)
    if not await lock.acquire():raise ApiError(409,"GENERATION_IN_PROGRESS","This generation request is already being processed.")
    try:
        person,garment=await asyncio.gather(validate_image(person_image,principal.is_paid),validate_image(garment_image,principal.is_paid))
        return clean(await start_generation(principal,person,garment,idempotency_key,request.state.request_id))
    finally:
        try:await lock.release()
        except Exception:pass


@app.get("/api/generations/{generation_id}")
async def get_generation(generation_id:str,request:Request):
    principal=await optional_principal(request,False)
    if not principal:raise ApiError(404,"GENERATION_NOT_FOUND","That generation could not be found.")
    row=await owns_generation(principal,generation_id)
    return clean(await serialize_generation(row,principal))


@app.websocket("/ws/generations/{generation_id}")
async def generation_socket(ws:WebSocket,generation_id:str):
    principal=await websocket_principal(ws)
    if not principal:
        await ws.close(code=4401);return
    ws_limit=int((await setting_values(["websocket_connection_rate_limit"])).get("websocket_connection_rate_limit",12))
    try:await rate_limit("websocket",principal.id,ws_limit,60)
    except ApiError:
        await ws.close(code=4429);return
    try: row=await owns_generation(principal,generation_id)
    except ApiError:
        await ws.close(code=4404);return
    await ws.accept();await ws.send_json({"event":f"generation.{row['status']}","generation":clean(await serialize_generation(row,principal))})
    pubsub=get_redis().pubsub();await pubsub.subscribe(f"generation:{generation_id}")
    try:
        async for message in pubsub.listen():
            if message["type"]!="message":continue
            row=await owns_generation(principal,generation_id);event=json.loads(message["data"])["event"]
            await ws.send_json({"event":event,"generation":clean(await serialize_generation(row,principal))})
            if row["status"] in ("completed","failed","cancelled","expired"):break
    except WebSocketDisconnect:pass
    finally:await pubsub.unsubscribe(f"generation:{generation_id}");await pubsub.aclose()


@app.get("/api/ads/{slug}")
async def get_ad(slug:str,request:Request):
    principal=await optional_principal(request)
    placement=await active_ad(slug,principal)
    if placement:await get_pool().execute("insert into ad_events(placement_id,guest_id,user_id,event_type) values($1,$2,$3,'ad_slot_requested')",placement["id"],principal.id if principal.kind=="guest" else None,principal.id if principal.kind=="user" else None)
    return {"placement":clean(placement)}


@app.post("/api/ads/events",status_code=204)
async def record_ad_event(request:Request,payload:dict[str,Any]):
    event=payload.get("event")
    if event not in {"ad_slot_rendered","ad_slot_visible"}:raise ApiError(400,"INVALID_EVENT","Unknown ad event.")
    placement=await get_pool().fetchrow("select id from ad_placements where id=$1 and is_active and deleted_at is null",payload.get("placement_id"))
    if not placement:raise ApiError(404,"AD_NOT_FOUND","Ad placement not found.")
    principal=await optional_principal(request)
    if principal.kind=="user" and principal.is_paid:return Response(status_code=204)
    await get_pool().execute("insert into ad_events(placement_id,guest_id,user_id,event_type) values($1,$2,$3,$4)",placement["id"],principal.id if principal.kind=="guest" else None,principal.id if principal.kind=="user" else None,event)
    return Response(status_code=204)


@app.post("/api/analytics",status_code=204)
async def analytics(request:Request,payload:dict[str,Any]=Body(...)):
    allowed={"landing_view","try_free_clicked","guest_created","guest_trial_blocked","image_upload_started","image_upload_completed","image_upload_rejected","generation_started","generation_completed","generation_failed","free_generation_used","free_limit_reached","pricing_viewed","signup_started","signup_completed","login_completed","checkout_started","payment_completed","credit_used","download_clicked"}
    event=str(payload.get("event",""))
    if event not in allowed:raise ApiError(400,"INVALID_EVENT","Unknown analytics event.")
    principal=await optional_principal(request)
    metadata={k:v for k,v in dict(payload.get("metadata") or {}).items() if k not in {"image","email","token","authorization"}}
    await get_pool().execute("insert into analytics_events(event_name,user_id,guest_id,session_id,metadata) values($1,$2,$3,$4,$5)",event,principal.id if principal.kind=="user" else None,principal.id if principal.kind=="guest" else None,hash_value(request.cookies.get(COOKIE_NAME,""),"analytics"),json.dumps(metadata))
    return Response(status_code=204)


@app.post("/api/auth/preflight/{action}",status_code=204)
async def auth_check(action:str,payload:EmailPayload,request:Request):
    if action not in {"login","signup","password-reset"}:raise ApiError(404,"NOT_FOUND","Route not found.")
    values=await setting_values(["signup_rate_limit","password_reset_rate_limit"]);ip=request_ip(request);device=request.headers.get("x-device-token","");rules={"login":(10,300),"signup":(int(values.get("signup_rate_limit",5)),3600),"password-reset":(int(values.get("password_reset_rate_limit",3)),3600)};limit,window=rules[action]
    await rate_limit(action,ip,limit,window);await rate_limit(action,device or ip,limit,window)
    if action=="password-reset":await rate_limit(action,str(payload.email).lower(),3,3600)
    return Response(status_code=204)


class LoginPayload(BaseModel):
    email:EmailStr
    password:str


@app.post("/api/auth/login")
async def secure_login(payload:LoginPayload,request:Request):
    ip=request_ip(request);device=request.headers.get("x-device-token","") or ip
    security_cfg=await setting_values(["login_failed_attempts","login_cooldown_minutes"]);attempts=int(security_cfg.get("login_failed_attempts",5));cooldown=int(security_cfg.get("login_cooldown_minutes",15))*60
    await rate_limit("login",ip,attempts*2,300);await rate_limit("login-device",device,attempts*2,300)
    failure_key=f"login-fail:{hash_value(str(payload.email).lower()+':'+ip,'login')}"
    if int(await get_redis().get(failure_key) or 0)>=attempts:raise ApiError(429,"LOGIN_COOLDOWN","Too many failed attempts. Try again later.")
    cfg=settings()
    async with httpx.AsyncClient(timeout=10) as client:
        response=await client.post(f"{cfg.supabase_url}/auth/v1/token?grant_type=password",json={"email":str(payload.email),"password":payload.password},headers={"apikey":cfg.supabase_service_role_key})
    if response.status_code!=200:
        failures=await get_redis().incr(failure_key);await get_redis().expire(failure_key,cooldown)
        if failures>=attempts:await get_pool().execute("insert into security_events(event_type,ip_hash,metadata) values('login_rate_limited',$1,$2)",hash_value(ip,"ip-cluster"),json.dumps({"email_hash":hash_value(str(payload.email).lower(),"email")}))
        raise ApiError(401,"INVALID_CREDENTIALS","Email or password is incorrect.")
    await get_redis().delete(failure_key)
    body=response.json()
    return {"access_token":body["access_token"],"refresh_token":body["refresh_token"],"expires_in":body.get("expires_in",3600)}


@app.post("/api/auth/convert-guest")
async def convert_guest(request:Request,user:Principal=Depends(require_user)):
    token=unsign_guest(request.cookies.get(COOKIE_NAME))
    if not token:return {"converted":False}
    guest=await get_pool().fetchrow("select id,converted_user_id from guest_sessions where public_guest_token_hash=$1",hash_value(token,"guest"))
    if not guest:return {"converted":False}
    if guest["converted_user_id"] and str(guest["converted_user_id"])!=user.id:raise ApiError(409,"GUEST_ALREADY_CONVERTED","This guest session already belongs to another account.")
    converted=await get_pool().fetchval("select convert_guest_to_user($1,$2)",guest["id"],user.id)
    return {"converted":bool(converted)}


@app.get("/api/account/overview")
async def account_overview(request:Request,user:Principal=Depends(require_user)):
    profile=await get_pool().fetchrow("select email,display_name,created_at from profiles where id=$1",user.id)
    wallet=await get_pool().fetchrow("select paid_credits,bonus_credits,reserved_credits from credit_wallets where user_id=$1",user.id)
    generations=await get_pool().fetch("select * from generations where user_id=$1 order by created_at desc limit 100",user.id)
    output=[]
    for row in generations:output.append(await serialize_generation(dict(row),user))
    payments=await get_pool().fetch("select id,amount,currency,status,created_at,package_name_snapshot from payments where user_id=$1 order by created_at desc",user.id)
    txs=await get_pool().fetch("select id,type,amount,reason,created_at from credit_transactions where user_id=$1 order by created_at desc limit 100",user.id)
    return clean({"profile":dict(profile),"wallet":dict(wallet or {"paid_credits":0,"bonus_credits":0,"reserved_credits":0}),"generations":output,"payments":[dict(x) for x in payments],"transactions":[dict(x) for x in txs]})


@app.post("/api/account/export/request",status_code=202)
async def request_export(request:Request,user:Principal=Depends(require_user)):
    await get_pool().execute("insert into account_requests(user_id,type,status) values($1,'export','queued')",user.id)
    return {"status":"queued"}


@app.get("/api/account/export")
async def export_account(user:Principal=Depends(require_user)):
    profile=await get_pool().fetchrow("select id,email,display_name,account_status,preferred_currency,preferred_locale,timezone,created_at,last_seen_at,last_login_at,email_verified_at,first_paid_at,is_paid from profiles where id=$1",user.id)
    generations=await get_pool().fetch("select id,status,provider,credit_source,estimated_provider_cost,actual_provider_cost,created_at,completed_at,expired_at from generations where user_id=$1",user.id)
    payments=await get_pool().fetch("select id,amount,currency,provider,status,credits_to_grant,package_name_snapshot,generation_count_snapshot,price_snapshot,currency_snapshot,discount_snapshot,created_at from payments where user_id=$1",user.id)
    credits=await get_pool().fetch("select id,type,amount,balance_before,balance_after,reason,created_at from credit_transactions where user_id=$1",user.id)
    sessions=await get_pool().fetch("select id,session_started_at,last_seen_at,session_ended_at,ip_country,device_type,browser_family,os_family,login_method,is_suspicious from user_sessions where user_id=$1",user.id)
    content=json.dumps(clean({"exported_at":datetime.now().isoformat(),"profile":dict(profile),"generations":[dict(x) for x in generations],"payments":[dict(x) for x in payments],"credit_history":[dict(x) for x in credits],"account_activity":[dict(x) for x in sessions]}),indent=2)
    return Response(content,media_type="application/json",headers={"Content-Disposition":"attachment; filename=fitted-data-export.json"})


@app.post("/api/account/delete",status_code=202)
async def delete_account(request:Request,confirmation:str=Body(embed=True),user:Principal=Depends(require_user)):
    if confirmation!="DELETE":raise ApiError(400,"CONFIRMATION_REQUIRED","Type DELETE to confirm.")
    await get_pool().execute("insert into account_requests(user_id,type,status) values($1,'delete','queued')",user.id)
    return {"status":"queued"}


@app.post("/api/account/sessions/revoke")
async def revoke_sessions(request:Request,user:Principal=Depends(require_user)):
    cfg=settings()
    async with __import__('httpx').AsyncClient(timeout=10) as client:
        await client.post(f"{cfg.supabase_url}/auth/v1/admin/users/{user.id}/logout",headers={"Authorization":f"Bearer {cfg.supabase_service_role_key}","apikey":cfg.supabase_service_role_key})
    await get_pool().execute("update user_sessions set session_ended_at=now() where user_id=$1 and session_ended_at is null",user.id)
    return {"revoked":True}


class CheckoutBody(BaseModel): package_id:str
@app.post("/api/payments/checkout")
async def checkout(payload:CheckoutBody,request:Request,user:Principal=Depends(require_user)):
    payment_limit=int((await setting_values(["payment_rate_limit"])).get("payment_rate_limit",5));await rate_limit("payment",user.id,payment_limit,60)
    verified=await get_pool().fetchval("select email_verified_at is not null from profiles where id=$1",user.id)
    if not verified:raise ApiError(403,"EMAIL_VERIFICATION_REQUIRED","Verify your email before purchasing credits.")
    pkg=await get_pool().fetchrow("select * from credit_packages where id=$1 and is_active and (starts_at is null or starts_at<=now()) and (ends_at is null or ends_at>now())",payload.package_id)
    if not pkg:raise ApiError(404,"PACKAGE_NOT_FOUND","That package is no longer available.")
    if settings().payment_provider=="manual_test" and settings().production:raise ApiError(503,"PAYMENTS_NOT_CONFIGURED","Checkout is temporarily unavailable.")
    payment_id=await get_pool().fetchval("""insert into payments(user_id,package_id,amount,currency,provider,status,credits_to_grant,package_name_snapshot,generation_count_snapshot,price_snapshot,currency_snapshot,discount_snapshot)
      values($1,$2,$3,$4,$5,'pending',$6,$7,$6,$3,$4,$8) returning id""",user.id,pkg["id"],pkg["price"],pkg["currency"],settings().payment_provider,pkg["generation_count"],pkg["name"],pkg["discount_percent"])
    return {"payment_id":str(payment_id),"checkout_url":None,"provider":settings().payment_provider}


@app.post("/api/payments/webhook/{provider}")
async def payment_webhook(provider:str,request:Request):
    body=await request.body()
    if not verify_webhook_signature(body,request.headers.get("x-webhook-signature")):raise ApiError(400,"PAYMENT_VERIFICATION_FAILED","Webhook verification failed.")
    payload=json.loads(body);payment_id=await grant_verified_payment(payload,provider)
    return {"received":True,"payment_id":payment_id}


def require_admin(user:Principal=Depends(require_user))->Principal:
    if user.role not in {"support_admin","admin","super_admin"}:raise ApiError(403,"ADMIN_REQUIRED","Administrator access is required.")
    return user


def require_full_admin(user:Principal=Depends(require_user))->Principal:
    if user.role not in {"admin","super_admin"}:raise ApiError(403,"ADMIN_REQUIRED","Administrator access is required.")
    return user


@app.get("/api/admin/pricing")
async def admin_pricing(_:Principal=Depends(require_full_admin)):
    rows=await get_pool().fetch("select * from credit_packages order by sort_order")
    return clean({"packages":[dict(r) for r in rows]})


@app.post("/api/admin/pricing")
async def save_pricing(request:Request,payload:dict[str,Any],admin:Principal=Depends(require_full_admin)):
    before=None
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            if payload.get("id"):before=await conn.fetchrow("select * from credit_packages where id=$1 for update",payload["id"])
            row=await conn.fetchrow("select * from upsert_credit_package($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",payload.get("id"),payload["name"],payload["slug"],payload["generation_count"],payload["price"],payload["currency"],payload.get("original_price"),payload.get("discount_percent",0),payload.get("badge"),payload.get("description"),payload.get("is_active",True),payload.get("is_featured",False),payload.get("sort_order",0))
            await conn.execute("insert into admin_audit_logs(admin_id,action,entity_type,entity_id,before_json,after_json,reason,ip_hash) values($1,'pricing.update','credit_package',$2,$3,$4,$5,$6)",admin.id,row["id"],json.dumps(clean(dict(before))) if before else None,json.dumps(clean(dict(row))),payload.get("reason","Pricing administration"),hash_value(request_ip(request),"admin-ip"))
    return clean(dict(row))


@app.patch("/api/admin/settings/{key}")
async def update_setting(key:str,request:Request,payload:dict[str,Any],admin:Principal=Depends(require_user)):
    if admin.role!="super_admin":raise ApiError(403,"SUPER_ADMIN_REQUIRED","Super administrator access is required.")
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            before=await conn.fetchrow("select * from system_settings where key=$1 for update",key)
            if not before:raise ApiError(404,"SETTING_NOT_FOUND","Setting not found.")
            await conn.execute("update system_settings set value=$2,updated_at=now(),updated_by=$3 where key=$1",key,json.dumps(payload["value"]),admin.id)
            await conn.execute("insert into admin_audit_logs(admin_id,action,entity_type,entity_id,before_json,after_json,reason,ip_hash) values($1,'setting.update','system_setting',$2,$3,$4,$5,$6)",admin.id,key,json.dumps(clean(dict(before))),json.dumps({"key":key,"value":payload["value"]}),payload.get("reason","System administration"),hash_value(request_ip(request),"admin-ip"))
    return {"key":key,"value":payload["value"]}


@app.post("/api/admin/users/{user_id}/credits")
async def adjust_credits(user_id:str,request:Request,payload:dict[str,Any],admin:Principal=Depends(require_full_admin)):
    amount=int(payload.get("amount",0));reason=str(payload.get("reason","")).strip()
    if not amount or not reason:raise ApiError(400,"INVALID_ADJUSTMENT","A non-zero amount and reason are required.")
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            wallet=await conn.fetchrow("select * from credit_wallets where user_id=$1 for update",user_id)
            if not wallet or wallet["paid_credits"]+amount<0:raise ApiError(400,"INVALID_ADJUSTMENT","The adjustment would create a negative balance.")
            before=wallet["paid_credits"]+wallet["bonus_credits"]-wallet["reserved_credits"]
            await conn.execute("update credit_wallets set paid_credits=paid_credits+$2,updated_at=now() where user_id=$1",user_id,amount)
            tx=await conn.fetchval("insert into credit_transactions(user_id,type,amount,balance_before,balance_after,reason,admin_id) values($1,'admin_adjustment',$2,$3,$4,$5,$6) returning id",user_id,amount,before,before+amount,reason,admin.id)
            await conn.execute("insert into admin_audit_logs(admin_id,action,entity_type,entity_id,before_json,after_json,reason,ip_hash) values($1,'credits.adjust','credit_wallet',$2,$3,$4,$5,$6)",admin.id,user_id,json.dumps(clean(dict(wallet))),json.dumps({"transaction_id":str(tx),"amount":amount}),reason,hash_value(request_ip(request),"admin-ip"))
    return {"transaction_id":str(tx)}


@app.post("/api/admin/users/{user_id}/suspension")
async def suspend_user(user_id:str,request:Request,payload:dict[str,Any],admin:Principal=Depends(require_full_admin)):
    status=payload.get("status");reason=str(payload.get("reason","")).strip()
    if status not in {"active","restricted","suspended"} or not reason:raise ApiError(400,"INVALID_SUSPENSION","A valid status and reason are required.")
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            before=await conn.fetchrow("select account_status,suspended_at,suspension_reason from profiles where id=$1 for update",user_id)
            if not before:raise ApiError(404,"USER_NOT_FOUND","User not found.")
            await conn.execute("update profiles set account_status=$2,suspended_at=case when $2='suspended' then now() else null end,suspension_reason=case when $2='active' then null else $3 end,updated_at=now() where id=$1",user_id,status,reason)
            await conn.execute("insert into admin_audit_logs(admin_id,action,entity_type,entity_id,before_json,after_json,reason,ip_hash) values($1,'user.suspension','profile',$2,$3,$4,$5,$6)",admin.id,user_id,json.dumps(clean(dict(before))),json.dumps({"account_status":status}),reason,hash_value(request_ip(request),"admin-ip"))
    return {"status":status}


@app.post("/api/admin/ads")
async def save_ad(request:Request,payload:dict[str,Any],admin:Principal=Depends(require_full_admin)):
    ad_id=payload.get("id") or str(uuid.uuid4());before=None
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            if payload.get("id"):before=await conn.fetchrow("select * from ad_placements where id=$1 for update",ad_id)
            row=await conn.fetchrow("""insert into ad_placements(id,name,slug,provider,ad_unit_id,custom_channel_id,location,is_active,priority,frequency_mode,max_per_session,device_target,starts_at,ends_at)
              values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) on conflict(id) do update set name=excluded.name,slug=excluded.slug,provider=excluded.provider,ad_unit_id=excluded.ad_unit_id,custom_channel_id=excluded.custom_channel_id,location=excluded.location,is_active=excluded.is_active,priority=excluded.priority,frequency_mode=excluded.frequency_mode,max_per_session=excluded.max_per_session,device_target=excluded.device_target,starts_at=excluded.starts_at,ends_at=excluded.ends_at,updated_at=now() returning *""",ad_id,payload["name"],payload["slug"],payload["provider"],payload["ad_unit_id"],payload.get("custom_channel_id"),payload["location"],bool(payload.get("is_active",False)),int(payload.get("priority",0)),payload.get("frequency_mode","session"),int(payload.get("max_per_session",1)),payload.get("device_target","all"),payload.get("starts_at"),payload.get("ends_at"))
            await conn.execute("insert into admin_audit_logs(admin_id,action,entity_type,entity_id,before_json,after_json,reason,ip_hash) values($1,'ad.update','ad_placement',$2,$3,$4,$5,$6)",admin.id,ad_id,json.dumps(clean(dict(before))) if before else None,json.dumps(clean(dict(row))),payload.get("reason","Ad administration"),hash_value(request_ip(request),"admin-ip"))
    return clean(dict(row))


@app.delete("/api/admin/ads/{ad_id}")
async def delete_ad(ad_id:str,request:Request,payload:dict[str,Any],admin:Principal=Depends(require_full_admin)):
    reason=str(payload.get("reason","")).strip()
    if not reason:raise ApiError(400,"REASON_REQUIRED","A deletion reason is required.")
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            before=await conn.fetchrow("select * from ad_placements where id=$1 for update",ad_id)
            if not before:raise ApiError(404,"AD_NOT_FOUND","Ad placement not found.")
            await conn.execute("update ad_placements set is_active=false,deleted_at=now(),updated_at=now() where id=$1",ad_id)
            await conn.execute("insert into admin_audit_logs(admin_id,action,entity_type,entity_id,before_json,reason,ip_hash) values($1,'ad.delete','ad_placement',$2,$3,$4,$5)",admin.id,ad_id,json.dumps(clean(dict(before))),reason,hash_value(request_ip(request),"admin-ip"))
    return Response(status_code=204)


ADMIN_QUERIES={
"overview":("select (select count(*) from profiles) users,(select count(*) from guest_sessions) guests,(select count(*) from profiles where is_paid) paying_users,(select count(*) from generations where status in ('queued','processing')) active_generations,(select coalesce(sum(amount),0) from financial_events where event_type='package_sale') revenue","select id,email,is_paid,created_at from profiles order by created_at desc limit 20"),
"users":("select count(*) total_users from profiles","select id,email,account_status,role,is_paid,risk_level,created_at from profiles order by created_at desc"),
"guests":("select count(*) guest_sessions,count(*) filter(where free_generations_allowed>0) eligible_free_trials,count(*) filter(where free_generations_allowed=0) blocked_abuse_attempts,coalesce(sum(free_generations_used),0) free_generations,coalesce(sum(free_generations_used*(select (value#>>'{}')::numeric from system_settings where key='fal_estimated_cost_per_generation')),0) ai_cost_of_free_trial,count(*) filter(where converted_user_id is not null) signups from guest_sessions","select id,created_at,last_seen_at,free_generations_used,risk_score,abuse_status,device_type from guest_sessions order by created_at desc"),
"generations":("select count(*) generations from generations","select id,status,provider,credit_source,created_at,completed_at from generations order by created_at desc"),
"promotions":("select count(*) promotions from promotions","select id,name,code,discount_percent,is_active,starts_at,ends_at from promotions order by created_at desc"),
"ads":("select count(*) active_ads from ad_placements where is_active and deleted_at is null","select id,name,slug,provider,ad_unit_id,location,is_active,priority from ad_placements where deleted_at is null order by priority desc"),
"revenue":("with f as(select coalesce(sum(amount) filter(where event_type='package_sale'),0) package_revenue,coalesce(sum(amount) filter(where event_type='advertising_revenue'),0) ad_revenue,coalesce(sum(amount) filter(where event_type='refund'),0) refunds,coalesce(sum(amount) filter(where event_type='provider_ai_cost'),0) fal_ai_costs,coalesce(sum(amount) filter(where event_type='payment_fee'),0) payment_fees,coalesce(sum(amount) filter(where event_type='manual_operating_expense'),0) other_expenses from financial_events) select *,package_revenue+ad_revenue-refunds-fal_ai_costs gross_profit,package_revenue+ad_revenue-refunds-fal_ai_costs-payment_fees-other_expenses net_estimated_profit from f","select event_type,amount,currency,source,created_at from financial_events order by created_at desc"),
"unit-economics":("select coalesce((select sum(amount)/nullif(sum(credits_to_grant),0) from payments where status='paid'),0) average_sale_price_per_generation,coalesce(avg(actual_provider_cost) filter(where actual_provider_cost is not null),avg(estimated_provider_cost),0) fal_cost_per_generation,coalesce((select sum(amount) from payments where status='paid')/nullif((select count(*) from profiles where is_paid),0),0) revenue_per_paying_user,count(*) filter(where credit_source='free_trial') free_generations,count(*) filter(where status='failed') failed_generations,count(*) filter(where status='completed') successful_generations from generations","select status,count(*) count from generations group by status"),
"analytics":("select count(*) filter(where event_name='landing_view') landing_visitors,count(*) filter(where event_name='try_free_clicked') try_free,count(*) filter(where event_name='image_upload_completed') successful_uploads,count(*) filter(where event_name='generation_completed') generations_completed,count(*) filter(where event_name='pricing_viewed') pricing_views,count(*) filter(where event_name='signup_completed') signups,count(*) filter(where event_name='checkout_started') checkouts,count(*) filter(where event_name='payment_completed') paid from analytics_events","select event_name,count(*) count from analytics_events group by event_name order by count(*) desc"),
"security":("select count(*) security_events from security_events","select event_type,risk_score,ip_hash,created_at from security_events order by created_at desc"),
"audit":("select count(*) audited_actions from admin_audit_logs","select action,entity_type,entity_id,admin_id,reason,created_at from admin_audit_logs order by created_at desc"),
"settings":("select count(*) settings from system_settings","select key,value,description,updated_at from system_settings order by key"),
}


@app.get("/api/admin/{view}")
async def admin_view(view:str,admin:Principal=Depends(require_admin)):
    if view=="system-health":
        metrics={"api":"healthy","redis":"healthy" if await get_redis().ping() else "degraded","queue_length":await get_redis().zcard("arq:queue"),"websocket_connections":int(await get_redis().get("metrics:websockets") or 0)}
        rows=await get_pool().fetch("select error_code,safe_error_message,created_at from generations where status='failed' order by created_at desc limit 30")
        return clean({"metrics":metrics,"rows":[dict(r) for r in rows]})
    query=ADMIN_QUERIES.get(view)
    if not query:raise ApiError(404,"NOT_FOUND","Admin view not found.")
    if view in {"revenue","unit-economics","settings"} and admin.role not in {"admin","super_admin"}:raise ApiError(403,"ADMIN_REQUIRED","Higher administrator access is required.")
    metric_row=await get_pool().fetchrow(query[0]);rows=await get_pool().fetch(query[1])
    return clean({"metrics":dict(metric_row or {}),"rows":[dict(r) for r in rows]})
