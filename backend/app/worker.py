import os
from datetime import datetime
from typing import Any

import fal_client
import httpx
from arq import cron
from arq.connections import RedisSettings

from .config import settings
from .db import connect, disconnect, get_pool, get_redis, setting_values, transaction
from .services import publish_generation, signed_url, storage_delete, storage_upload


async def startup(_:dict[str,Any]) -> None:
    os.environ["FAL_KEY"]=settings().fal_key
    await connect()


async def shutdown(_:dict[str,Any]) -> None:
    await disconnect()


async def process_generation(_:dict[str,Any],generation_id:str) -> None:
    redis=get_redis();lock=redis.lock(f"lock:generation:{generation_id}",timeout=1800,blocking_timeout=1)
    if not await lock.acquire():return
    try:
        row=await get_pool().fetchrow("select * from generations where id=$1",generation_id)
        if not row or row["status"] not in ("queued","processing"):return
        if not settings().fal_key:raise RuntimeError("fal.ai is not configured")
        await get_pool().execute("update generations set status='processing',started_at=coalesce(started_at,now()),provider_request_started_at=coalesce(provider_request_started_at,now()) where id=$1",generation_id)
        await publish_generation(generation_id,"generation.processing")
        person_url=await signed_url(row["person_image_path"],1800);garment_url=await signed_url(row["garment_image_path"],1800)
        if row["provider_job_id"]:
            result=await fal_client.result_async(settings().fal_model,row["provider_job_id"])
        else:
            handler=await fal_client.submit_async(settings().fal_model,arguments={"model_image":person_url,"garment_image":garment_url,"category":"auto","mode":"balanced","garment_photo_type":"auto"})
            await get_pool().execute("update generations set provider_job_id=$2 where id=$1 and provider_job_id is null",generation_id,handler.request_id)
            result=await handler.get()
        images=result.get("images") or ([result["image"]] if result.get("image") else [])
        if not images or not images[0].get("url"):raise RuntimeError("fal.ai returned no result image")
        async with httpx.AsyncClient(timeout=60,follow_redirects=True) as client:
            response=await client.get(images[0]["url"]);response.raise_for_status();data=response.content
        result_path=(f"users/{row['user_id']}" if row["user_id"] else f"guest/{row['guest_id']}")+f"/result/{generation_id}.png"
        await storage_upload(result_path,data,response.headers.get("content-type","image/png"))
        async with transaction() as conn:
            current=await conn.fetchrow("select status from generations where id=$1 for update",generation_id)
            if current["status"]=="completed":return
            if current["status"] not in ("queued","processing"):
                await storage_delete([result_path]);return
            await conn.execute("update generations set status='completed',result_image_path=$2,provider_response_received_at=now(),completed_at=now() where id=$1",generation_id,result_path)
            if row["user_id"]:await conn.fetchrow("select * from capture_paid_credit($1,$2)",row["user_id"],generation_id)
            await conn.execute("insert into financial_events(event_type,amount,currency,source,reference_id) values('provider_ai_cost',$1,'USD','fal.ai',$2)",row["estimated_provider_cost"],generation_id)
        await publish_generation(generation_id,"generation.completed")
    except Exception as exc:
        # Once fal accepted the request, retries are unsafe. Reconciliation uses provider_job_id.
        current=await get_pool().fetchrow("select * from generations where id=$1",generation_id)
        ambiguous=bool(current and current["provider_job_id"])
        if not ambiguous:
            async with transaction() as conn:
                await conn.execute("update generations set status='failed',error_code='GENERATION_FAILED',safe_error_message='We could not create this look. Your usage was restored when appropriate.',internal_error_detail=$2,completed_at=now() where id=$1 and status!='completed'",generation_id,str(exc)[:1000])
                if current and current["user_id"]:await conn.fetchrow("select * from refund_paid_credit($1,$2,'provider_failed_before_billing')",current["user_id"],generation_id)
                elif current:await conn.execute("select refund_guest_generation($1,$2)",current["guest_id"],generation_id)
            await publish_generation(generation_id,"generation.failed")
        else:
            await get_pool().execute("update generations set internal_error_detail=$2 where id=$1",generation_id,f"ambiguous_provider_state:{str(exc)[:900]}")
            raise
    finally:
        try:await lock.release()
        except Exception:pass


async def reconcile_stuck(_:dict[str,Any]) -> None:
    cfg=await setting_values(["generation_stuck_timeout_minutes"]);minutes=int(cfg.get("generation_stuck_timeout_minutes",20))
    rows=await get_pool().fetch("select * from generations where status in ('queued','processing') and queued_at<now()-make_interval(mins=>$1)",minutes)
    for row in rows:
        if row["provider_job_id"]:
            try:
                status=await fal_client.status_async(settings().fal_model,row["provider_job_id"])
                if status.__class__.__name__=="Completed":
                    await get_redis().enqueue_job("process_generation",str(row["id"]),_job_id=f"recovery:{row['id']}:{int(datetime.now().timestamp())//900}")
                    await publish_generation(str(row["id"]),"generation.recovered")
                    continue
            except Exception:continue
        if row["status"]=="queued" and not row["provider_job_id"]:
            await get_redis().enqueue_job("process_generation",str(row["id"]),_job_id=f"requeue:{row['id']}")


async def cleanup_expired(_:dict[str,Any]) -> None:
    cfg=await setting_values(["guest_upload_retention_hours","guest_result_retention_hours","paid_upload_retention_days","paid_result_retention_days"])
    uploads=await get_pool().fetch("""select id,person_image_path,garment_image_path from generations where person_image_path<>'' and ((guest_id is not null and created_at<now()-make_interval(hours=>$1)) or (user_id is not null and created_at<now()-make_interval(days=>$2))) limit 500""",int(cfg.get("guest_upload_retention_hours",24)),int(cfg.get("paid_upload_retention_days",30)))
    for row in uploads:
        await storage_delete([p for p in (row["person_image_path"],row["garment_image_path"]) if p]);await get_pool().execute("update generations set person_image_path='',garment_image_path='' where id=$1",row["id"])
    results=await get_pool().fetch("""select id,result_image_path from generations where result_image_path is not null and expired_at is null and ((guest_id is not null and created_at<now()-make_interval(hours=>$1)) or (user_id is not null and created_at<now()-make_interval(days=>$2))) limit 500""",int(cfg.get("guest_result_retention_hours",24)),int(cfg.get("paid_result_retention_days",30)))
    for row in results:
        await storage_delete([row["result_image_path"]]);await get_pool().execute("update generations set status=case when status='completed' then 'expired' else status end,expired_at=now(),result_image_path=null where id=$1",row["id"])


async def process_account_requests(_:dict[str,Any]) -> None:
    requests=await get_pool().fetch("select * from account_requests where status='queued' and type='delete' order by created_at for update skip locked limit 20")
    cfg=settings()
    for item in requests:
        await get_pool().execute("update account_requests set status='processing' where id=$1",item["id"])
        rows=await get_pool().fetch("select person_image_path,garment_image_path,result_image_path from generations where user_id=$1",item["user_id"])
        await storage_delete([path for row in rows for path in (row["person_image_path"],row["garment_image_path"],row["result_image_path"]) if path])
        async with transaction() as conn:
            await conn.execute("update generations set status=case when status in ('queued','processing') then 'cancelled' when status='completed' then 'expired' else status end,person_image_path='',garment_image_path='',result_image_path=null,person_image_hash='',garment_image_hash='',expired_at=now() where user_id=$1",item["user_id"])
            await conn.execute("update analytics_events set user_id=null,metadata='{}' where user_id=$1",item["user_id"])
            await conn.execute("delete from user_sessions where user_id=$1",item["user_id"])
            await conn.execute("update profiles set email=null,display_name='Deleted user',avatar_path=null,account_status='deleted',country_code=null,timezone=null,suspension_reason=null,updated_at=now() where id=$1",item["user_id"])
            await conn.execute("update account_requests set status='completed',completed_at=now() where id=$1",item["id"])
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{cfg.supabase_url}/auth/v1/admin/users/{item['user_id']}/logout",headers={"Authorization":f"Bearer {cfg.supabase_service_role_key}","apikey":cfg.supabase_service_role_key})


class WorkerSettings:
    functions=[process_generation]
    cron_jobs=[cron(reconcile_stuck,minute={0,15,30,45}),cron(cleanup_expired,hour={2},minute={10}),cron(process_account_requests,minute={5,20,35,50})]
    on_startup=startup;on_shutdown=shutdown
    redis_settings=RedisSettings.from_dsn(settings().redis_url)
    job_timeout=1800;max_tries=2;keep_result=3600;health_check_interval=30
