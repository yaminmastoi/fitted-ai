import hashlib
import hmac
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import settings
from .db import get_pool, get_redis, setting_values, transaction
from .security import ApiError, Principal

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
Image.MAX_IMAGE_PIXELS = 40_000_000


@dataclass
class SafeImage:
    data: bytes
    sha256: str
    width: int
    height: int
    mime: str = "image/webp"


async def validate_image(upload: UploadFile, paid: bool) -> SafeImage:
    cfg = await setting_values(["free_max_upload_mb","paid_max_upload_mb","free_min_width","free_min_height","free_max_width","free_max_height","max_image_megapixels"])
    max_mb=float(cfg.get("paid_max_upload_mb" if paid else "free_max_upload_mb",10 if paid else 2))
    if upload.content_type not in ALLOWED_MIME:
        raise ApiError(415,"INVALID_IMAGE","Use a JPG, PNG, or WEBP image.")
    raw=await upload.read(int(max_mb*1024*1024)+1)
    if len(raw)>max_mb*1024*1024: raise ApiError(413,"UPLOAD_TOO_LARGE",f"Each image must be {max_mb:g} MB or smaller.")
    if not raw: raise ApiError(400,"INVALID_IMAGE","The image is empty or corrupt.")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            actual=(source.format or "").upper()
            if actual not in {"JPEG","PNG","WEBP"}: raise ApiError(415,"INVALID_IMAGE","The file contents do not match a supported image type.")
            if getattr(source,"is_animated",False) or int(getattr(source,"n_frames",1))>1: raise ApiError(415,"INVALID_IMAGE","Animated images are not supported.")
            width,height=source.size; pixels=width*height
            if width<int(cfg.get("free_min_width",256)) or height<int(cfg.get("free_min_height",256)): raise ApiError(400,"INVALID_IMAGE","The image dimensions are too small.")
            if width>int(cfg.get("free_max_width",6000)) or height>int(cfg.get("free_max_height",6000)) or pixels>float(cfg.get("max_image_megapixels",24))*1_000_000: raise ApiError(413,"IMAGE_DIMENSIONS_TOO_LARGE","The image dimensions are too large.")
            source.load(); normalized=ImageOps.exif_transpose(source).convert("RGB")
            out=io.BytesIO(); normalized.save(out,"WEBP",quality=92,method=6,exif=b"")
            data=out.getvalue()
            return SafeImage(data,hashlib.sha256(data).hexdigest(),normalized.width,normalized.height)
    except ApiError: raise
    except (UnidentifiedImageError,Image.DecompressionBombError,OSError,ValueError): raise ApiError(400,"INVALID_IMAGE","The image is corrupt or unsafe.") from None


async def storage_upload(path: str, data: bytes, mime: str = "image/webp") -> None:
    cfg=settings(); url=f"{cfg.supabase_url}/storage/v1/object/{cfg.storage_bucket}/{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        response=await client.post(url,content=data,headers={"Authorization":f"Bearer {cfg.supabase_service_role_key}","apikey":cfg.supabase_service_role_key,"Content-Type":mime,"x-upsert":"false"})
    if response.status_code not in (200,201): raise ApiError(502,"STORAGE_ERROR","Secure image storage is temporarily unavailable.")


async def signed_url(path: str, expires: int = 900) -> str:
    cfg=settings(); url=f"{cfg.supabase_url}/storage/v1/object/sign/{cfg.storage_bucket}/{path}"
    async with httpx.AsyncClient(timeout=10) as client:
        response=await client.post(url,json={"expiresIn":expires},headers={"Authorization":f"Bearer {cfg.supabase_service_role_key}","apikey":cfg.supabase_service_role_key})
    if response.status_code!=200: raise ApiError(502,"STORAGE_ERROR","The private image link could not be created.")
    value=response.json()["signedURL"]
    return value if value.startswith("http") else f"{cfg.supabase_url}/storage/v1{value}"


async def storage_download(path: str) -> bytes:
    cfg=settings();url=f"{cfg.supabase_url}/storage/v1/object/authenticated/{cfg.storage_bucket}/{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        r=await client.get(url,headers={"Authorization":f"Bearer {cfg.supabase_service_role_key}","apikey":cfg.supabase_service_role_key})
    r.raise_for_status();return r.content


async def storage_delete(paths:list[str]) -> None:
    if not paths:return
    cfg=settings();url=f"{cfg.supabase_url}/storage/v1/object/{cfg.storage_bucket}"
    async with httpx.AsyncClient(timeout=30) as client:
        await client.request("DELETE",url,json={"prefixes":paths},headers={"Authorization":f"Bearer {cfg.supabase_service_role_key}","apikey":cfg.supabase_service_role_key})


async def start_generation(principal:Principal,person:SafeImage,garment:SafeImage,idempotency_key:str,request_id:str) -> dict[str,Any]:
    if not idempotency_key or len(idempotency_key)>128: raise ApiError(400,"INVALID_IDEMPOTENCY_KEY","A valid idempotency key is required.")
    cfg=await setting_values(["fal_estimated_cost_per_generation","max_active_generations_guest","max_active_generations_user"])
    owner_col="user_id" if principal.kind=="user" else "guest_id"
    existing=await get_pool().fetchrow(f"select * from generations where {owner_col}=$1 and idempotency_key=$2",principal.id,idempotency_key)
    if existing:return await serialize_generation(dict(existing),principal)
    limit=int(cfg.get("max_active_generations_user" if principal.kind=="user" else "max_active_generations_guest",3 if principal.kind=="user" else 1))
    active=await get_pool().fetchval(f"select count(*) from generations where {owner_col}=$1 and status in ('queued','processing')",principal.id)
    if active>=limit:raise ApiError(429,"GENERATION_RATE_LIMITED","You already have the maximum number of active try-ons.")
    generation_id=str(uuid.uuid4());prefix=f"users/{principal.id}" if principal.kind=="user" else f"guest/{principal.id}"
    person_path=f"{prefix}/person/{uuid.uuid4()}.webp";garment_path=f"{prefix}/garment/{uuid.uuid4()}.webp"
    await storage_upload(person_path,person.data);await storage_upload(garment_path,garment.data)
    try:
        async with transaction() as conn:
            if principal.kind=="guest":
                reservation=await conn.fetchrow("select * from reserve_guest_generation($1,$2,$3)",principal.id,generation_id,idempotency_key)
                if not reservation or not reservation["ok"]:raise ApiError(402,"FREE_TRIAL_EXHAUSTED","Your free trial is complete.")
                credit_source="free_trial";tx_id=None
            else:
                reservation=await conn.fetchrow("select * from reserve_paid_credit($1,$2,$3)",principal.id,generation_id,idempotency_key)
                if not reservation or not reservation["ok"]:raise ApiError(402,"INSUFFICIENT_CREDITS","You need another credit to create this look.")
                credit_source=reservation["source"];tx_id=reservation["transaction_id"]
            row=await conn.fetchrow("""insert into generations(id,guest_id,user_id,person_image_path,garment_image_path,person_image_hash,garment_image_hash,status,provider,credit_source,reserved_credit_transaction_id,estimated_provider_cost,queued_at,request_id,idempotency_key)
              values($1,$2,$3,$4,$5,$6,$7,'queued','fal.ai',$8,$9,$10,now(),$11,$12) returning *""",generation_id,principal.id if principal.kind=="guest" else None,principal.id if principal.kind=="user" else None,person_path,garment_path,person.sha256,garment.sha256,credit_source,tx_id,float(cfg.get("fal_estimated_cost_per_generation",0.075)),request_id,idempotency_key)
    except Exception:
        await storage_delete([person_path,garment_path]);raise
    # The generation and entitlement are already durable. If Redis is briefly unavailable,
    # reconciliation will enqueue it without losing the private inputs or double-reserving.
    try:await get_redis().enqueue_job("process_generation",generation_id,_job_id=f"generation:{generation_id}")
    except Exception:pass
    await publish_generation(generation_id,"generation.queued")
    return await serialize_generation(dict(row),principal)


async def owns_generation(principal:Principal,generation_id:str) -> dict[str,Any]:
    row=await get_pool().fetchrow("select * from generations where id=$1",generation_id)
    if not row:raise ApiError(404,"GENERATION_NOT_FOUND","That generation could not be found.")
    key="user_id" if principal.kind=="user" else "guest_id"
    if str(row[key] or "")!=principal.id:raise ApiError(404,"GENERATION_NOT_FOUND","That generation could not be found.")
    return dict(row)


async def serialize_generation(row:dict[str,Any],principal:Principal) -> dict[str,Any]:
    result={k:row.get(k) for k in ("id","status","safe_error_message","created_at")}
    for k,v in list(result.items()):
        if isinstance(v,datetime):result[k]=v.isoformat()
    if row.get("result_image_path") and row.get("status")=="completed":result["result_url"]=await signed_url(row["result_image_path"])
    if principal.kind=="guest":
        guest=await get_pool().fetchrow("select free_generations_allowed,free_generations_used from guest_sessions where id=$1",principal.id)
        result["remaining_free_generations"]=max(0,guest["free_generations_allowed"]-guest["free_generations_used"]) if guest else 0
    return result


async def publish_generation(generation_id:str,event:str) -> None:
    await get_redis().publish(f"generation:{generation_id}",json.dumps({"event":event,"generation_id":generation_id,"at":datetime.now(timezone.utc).isoformat()}))


async def active_ad(slug:str,principal:Principal) -> dict[str,Any]|None:
    if principal.kind=="user" and principal.is_paid:return None
    enabled=(await setting_values(["guest_ads_enabled"])).get("guest_ads_enabled",False)
    if not enabled:return None
    row=await get_pool().fetchrow("""select id,provider,ad_unit_id,custom_channel_id,location from ad_placements where slug=$1 and is_active and (starts_at is null or starts_at<=now()) and (ends_at is null or ends_at>now()) order by priority desc limit 1""",slug)
    return dict(row) if row else None


def verify_webhook_signature(body:bytes,provided:str|None) -> bool:
    secret=settings().payment_webhook_secret
    if not secret or not provided:return False
    expected=hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,provided)


async def grant_verified_payment(payload:dict[str,Any],provider:str) -> str:
    provider_payment_id=str(payload["provider_payment_id"]);package_id=str(payload["package_id"]);user_id=str(payload["user_id"])
    async with transaction() as conn:
        existing=await conn.fetchval("select id from payments where provider=$1 and provider_payment_id=$2",provider,provider_payment_id)
        if existing:return str(existing)
        pkg=await conn.fetchrow("select * from credit_packages where id=$1 and is_active",package_id)
        if not pkg:raise ApiError(400,"PAYMENT_VERIFICATION_FAILED","Payment package is invalid.")
        if str(payload.get("amount"))!=str(pkg["price"]) or payload.get("currency")!=pkg["currency"]:raise ApiError(400,"PAYMENT_VERIFICATION_FAILED","Payment amount did not match the server price.")
        payment_id=await conn.fetchval("""insert into payments(user_id,package_id,amount,currency,provider,provider_payment_id,status,credits_to_grant,package_name_snapshot,generation_count_snapshot,price_snapshot,currency_snapshot,discount_snapshot)
          values($1,$2,$3,$4,$5,$6,'paid',$7,$8,$9,$3,$4,$10) returning id""",user_id,package_id,pkg["price"],pkg["currency"],provider,provider_payment_id,pkg["generation_count"],pkg["name"],pkg["generation_count"],pkg["discount_percent"])
        await conn.fetchrow("select * from grant_purchased_credits($1,$2,$3)",user_id,payment_id,pkg["generation_count"])
        await conn.execute("insert into financial_events(event_type,amount,currency,source,reference_id) values('package_sale',$1,$2,$3,$4)",pkg["price"],pkg["currency"],provider,payment_id)
    return str(payment_id)
