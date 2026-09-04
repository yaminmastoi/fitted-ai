import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg
from arq.connections import ArqRedis, RedisSettings, create_pool

from .config import settings

pool: asyncpg.Pool | None = None
redis: ArqRedis | None = None


async def connect() -> None:
    global pool, redis
    cfg = settings()
    pool = await asyncpg.create_pool(cfg.database_url, min_size=2, max_size=20, command_timeout=30)
    redis = await create_pool(RedisSettings.from_dsn(cfg.redis_url))
    await redis.ping()


async def disconnect() -> None:
    global pool, redis
    if pool:
        await pool.close()
    if redis:
        await redis.aclose()
    pool = None
    redis = None


def get_pool() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("Database is unavailable")
    return pool


def get_redis() -> ArqRedis:
    if redis is None:
        raise RuntimeError("Redis is unavailable")
    return redis


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            yield conn


def record(value: asyncpg.Record | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return dict(value)


async def setting_values(keys: list[str] | None = None) -> dict[str, Any]:
    query = "select key, value from public.system_settings"
    args: list[Any] = []
    if keys:
        query += " where key = any($1::text[])"
        args.append(keys)
    rows = await get_pool().fetch(query, *args)
    result: dict[str, Any] = {}
    for row in rows:
        value = row["value"]
        result[row["key"]] = json.loads(value) if isinstance(value, str) else value
    return result
