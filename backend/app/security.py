import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from fastapi import HTTPException, Request, WebSocket
from pydantic import BaseModel, EmailStr

from .config import settings
from .db import get_pool, get_redis, setting_values

COOKIE_NAME = "fitted_guest"
RiskAction = Literal["ALLOW", "ALLOW_WITH_LOWER_RATE_LIMIT", "REQUIRE_EMAIL_VERIFICATION", "COOLDOWN", "DENY_FREE_TRIAL", "FLAG_FOR_REVIEW"]


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(status, {"code": code, "message": message, "details": details or {}})


def hash_value(value: str, purpose: str) -> str:
    key = settings().ip_hash_secret.encode()
    return hmac.new(key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def privacy_reduced_ip(raw: str) -> str:
    try:
        ip = ipaddress.ip_address(raw)
        if isinstance(ip, ipaddress.IPv4Address):
            parts = raw.split(".")
            return ".".join(parts[:3]) + ".0/24"
        return str(ipaddress.ip_network(f"{ip}/48", strict=False))
    except ValueError:
        return "unknown"


def request_ip(request: Request) -> str:
    cfg = settings()
    if cfg.trusted_proxy_count > 0:
        chain = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
        if len(chain) >= cfg.trusted_proxy_count:
            return chain[-cfg.trusted_proxy_count]
    return request.client.host if request.client else "unknown"


def sign_guest(token: str) -> str:
    encoded = base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")
    sig = hmac.new(settings().guest_cookie_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def unsign_guest(cookie: str | None) -> str | None:
    if not cookie or "." not in cookie:
        return None
    encoded, sig = cookie.rsplit(".", 1)
    expected = hmac.new(settings().guest_cookie_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except Exception:
        return None


@dataclass
class Principal:
    kind: Literal["guest", "user"]
    id: str
    role: str = "user"
    is_paid: bool = False
    email: str | None = None
    guest_token: str | None = None


async def verify_supabase_token(token: str) -> dict[str, Any]:
    cfg = settings()
    cache_key = f"auth:{hashlib.sha256(token.encode()).hexdigest()}"
    cached = await get_redis().get(cache_key)
    if cached:
        return json.loads(cached)
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(f"{cfg.supabase_url}/auth/v1/user", headers={"apikey": cfg.supabase_service_role_key, "Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        raise ApiError(401, "INVALID_SESSION", "Your session is no longer valid.")
    user = response.json()
    await get_redis().set(cache_key, json.dumps(user), ex=60)
    return user


async def optional_principal(request: Request, create_guest: bool = True) -> Principal | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        user = await verify_supabase_token(auth[7:])
        profile = await get_pool().fetchrow("select role, is_paid, email, account_status from profiles where id=$1", user["id"])
        if not profile:
            profile = await get_pool().fetchrow("select role, is_paid, email, account_status from ensure_profile($1,$2)", user["id"], user.get("email"))
        if profile and profile["account_status"] in ("suspended", "deleted"):
            raise ApiError(403, "ACCOUNT_SUSPENDED", "This account cannot perform that action.")
        return Principal("user", user["id"], profile["role"] if profile else "user", bool(profile and profile["is_paid"]), user.get("email"))
    token = unsign_guest(request.cookies.get(COOKIE_NAME))
    if token:
        row = await get_pool().fetchrow("select id,status from guest_sessions where public_guest_token_hash=$1 and expires_at>now()", hash_value(token, "guest"))
        if row and row["status"] == "active":
            return Principal("guest", str(row["id"]), guest_token=token)
    return None if not create_guest else await create_guest_principal(request)


async def require_user(request: Request) -> Principal:
    principal = await optional_principal(request, False)
    if not principal or principal.kind != "user":
        raise ApiError(401, "AUTH_REQUIRED", "Please log in to continue.")
    return principal


async def create_guest_principal(request: Request) -> Principal:
    cfg = await setting_values(["free_trial_generations", "free_trial_ip_window_days", "guest_creation_rate_limit"])
    ip_hash = hash_value(privacy_reduced_ip(request_ip(request)), "ip-cluster")
    await rate_limit("guest-create",ip_hash,int(cfg.get("guest_creation_rate_limit",6)),3600)
    device = request.headers.get("x-device-token", "")[:128]
    device_hash = hash_value(device, "device") if device else None
    risk = await evaluate_trial_risk(ip_hash, device_hash)
    token = secrets.token_urlsafe(32)
    allowance = int(cfg.get("free_trial_generations", 3)) if risk.action == "ALLOW" else (1 if risk.action == "ALLOW_WITH_LOWER_RATE_LIMIT" else 0)
    row = await get_pool().fetchrow("""
      insert into guest_sessions(public_guest_token_hash,expires_at,free_generations_allowed,risk_score,risk_level,abuse_status,ip_hash,device_hash,browser_family,os_family,device_type,timezone,locale)
      values($1,now()+interval '30 days',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) returning id
    """, hash_value(token,"guest"), allowance, risk.score, risk.level, risk.action, ip_hash, device_hash, ua_family(request.headers.get("user-agent",""))[0], ua_family(request.headers.get("user-agent",""))[1], request.headers.get("x-screen-category","unknown")[:32], request.headers.get("x-timezone","")[:80], request.headers.get("x-locale","")[:32])
    request.state.set_guest_cookie = sign_guest(token)
    if risk.action != "ALLOW":
        await security_event("trial_abuse_block" if allowance == 0 else "repeated_free_trial_attempt", None, str(row["id"]), ip_hash, {"score":risk.score,"action":risk.action})
    return Principal("guest", str(row["id"]), guest_token=token)


@dataclass
class RiskDecision:
    score: int
    level: str
    action: RiskAction


async def evaluate_trial_risk(ip_hash: str, device_hash: str | None) -> RiskDecision:
    cfg = await setting_values(["free_trial_ip_window_days"])
    days = int(cfg.get("free_trial_ip_window_days", 30))
    row = await get_pool().fetchrow("""select count(*) filter(where ip_hash=$1) ip_count, count(*) filter(where device_hash=$2 and $2 is not null) device_count,
      count(*) filter(where (ip_hash=$1 or (device_hash=$2 and $2 is not null)) and free_generations_used>0) used_count
      from guest_sessions where created_at > now()-make_interval(days=>$3)""", ip_hash, device_hash, days)
    score = min(100, int(row["ip_count"])*12 + int(row["device_count"])*24 + int(row["used_count"])*30)
    if int(row["device_count"]) >= 2 or int(row["used_count"]) >= 3: return RiskDecision(max(score,85),"critical","DENY_FREE_TRIAL")
    if score >= 70: return RiskDecision(score,"high","COOLDOWN")
    if score >= 40: return RiskDecision(score,"medium","ALLOW_WITH_LOWER_RATE_LIMIT")
    return RiskDecision(score,"low","ALLOW")


def ua_family(value: str) -> tuple[str,str]:
    v=value.lower(); browser=next((x for x in ("edge","chrome","firefox","safari") if x in v),"other")
    os=next((x for x in ("windows","android","iphone","mac os","linux") if x in v),"other")
    return browser,os


async def rate_limit(bucket: str, key: str, limit: int, window: int) -> None:
    redis = get_redis(); now=int(time.time()); redis_key=f"rl:{bucket}:{hash_value(key,'rate')}:{now//window}"
    count=await redis.incr(redis_key)
    if count==1: await redis.expire(redis_key,window+2)
    if count>limit: raise ApiError(429,"RATE_LIMITED","Too many requests. Please wait and try again.",{"retry_after":window-(now%window)})


async def security_event(event_type:str,user_id:str|None,guest_id:str|None,ip_hash:str|None,metadata:dict[str,Any]) -> None:
    await get_pool().execute("insert into security_events(event_type,user_id,guest_id,ip_hash,metadata) values($1,$2,$3,$4,$5)",event_type,user_id,guest_id,ip_hash,json.dumps(metadata))


async def websocket_principal(ws: WebSocket) -> Principal | None:
    token = ws.query_params.get("token")
    if token:
        user = await verify_supabase_token(token)
        p = await get_pool().fetchrow("select role,is_paid,email from profiles where id=$1", user["id"])
        return Principal("user",user["id"],p["role"] if p else "user",bool(p and p["is_paid"]),user.get("email"))
    guest = unsign_guest(ws.cookies.get(COOKIE_NAME))
    if guest:
        row=await get_pool().fetchrow("select id from guest_sessions where public_guest_token_hash=$1 and expires_at>now()",hash_value(guest,"guest"))
        if row:return Principal("guest",str(row["id"]),guest_token=guest)
    return None


class EmailPayload(BaseModel):
    email: EmailStr
