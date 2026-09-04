# Fitted — AI virtual try-on SaaS

Fitted is a standalone React/FastAPI application for privacy-conscious, credit-based virtual clothing try-on powered exclusively by fal.ai. The browser never receives `FAL_KEY`, Supabase service credentials, payment secrets, or internal risk data.

## Architecture

```text
React/Vite -> FastAPI -> PostgreSQL (durable truth)
                     -> Redis -> ARQ worker -> fal.ai
                     <- Redis pub/sub <- WebSocket + REST fallback
                     -> private Supabase Storage
```

ARQ was selected for the worker because it is async-native, small, Redis-backed, and works naturally with FastAPI. Billable provider calls are protected by database state, an idempotency key, and a Redis distributed lock. Retries occur only before a provider job ID is recorded; reconciliation handles ambiguous provider states.

## Local setup

Prerequisites: Node 20+, Python 3.12+, PostgreSQL/Supabase, and Redis 7+.

1. Copy `.env.example` to `.env` and fill in backend secrets. Copy `frontend/.env.example` to `frontend/.env` and provide only public values.
2. Apply `supabase/migrations/0001_initial.sql`, then `0002_storage.sql` in Supabase SQL Editor or with `supabase db push`.
3. Install and run:

   ```bash
   npm --prefix frontend install
   python -m venv .venv
   .venv/Scripts/pip install -e "backend[dev]"
   docker compose up redis -d
   uvicorn app.main:app --app-dir backend --reload --port 8000
   arq app.worker.WorkerSettings --watch backend
   npm --prefix frontend run dev
   ```

The API is at `http://localhost:8000`; the client is at `http://localhost:5173`.

## Production deployment

- Deploy `frontend/` as a static Vite build behind a CDN. Set the three public `VITE_*` variables at build time.
- Deploy the API and worker from the included `Dockerfile` as separate services. Run at least two API replicas and scale workers independently.
- Use managed Redis with TLS and Supabase connection pooling. Apply migrations before API rollout.
- Put the API behind a TLS proxy that preserves `X-Forwarded-For` only from trusted hops. Set exact `FRONTEND_URLS` values; never use wildcard CORS with credentials.
- Configure the private Supabase bucket `tryon-private`, fal.ai credentials, and payment webhook secret in the platform secret manager.
- Run `python -m app.maintenance cleanup` and `python -m app.maintenance reconcile` every 15 minutes from a scheduler.
- Payment checkout is provider-abstracted. The included `manual_test` adapter is development-only; implement a production adapter and signature verifier before accepting money.

## Security and data handling

- Guest entitlement is a durable database reservation, bound to a signed HttpOnly cookie and evaluated with privacy-reduced IP/device/browser signals. An IP is never a permanent identity or a paid-account blocker.
- Credits are reserved and settled only through locked PostgreSQL functions. Client-reported prices, credits, and payment status are ignored.
- Uploads are decoded with Pillow, dimension/pixel constrained, animation-rejected, orientation-normalized, metadata-stripped, and re-encoded before private upload.
- Supabase RLS blocks cross-user reads. Guests use backend routes only. Service-role and fal.ai keys are backend-only.
- Request IDs, security events, and admin audit records provide traceability without storing raw auth tokens.

## User data inventory

| Data | Purpose | Default retention | User deletion/export | Shared with |
|---|---|---|---|---|
| Profile and verified email | Account, support, billing | Account life | Export; delete/anonymize | Supabase Auth |
| Person/garment images | Generate requested try-on | Guest 24h; paid configurable | Delete | Supabase Storage, fal.ai |
| Generated result | Product result/history | Guest 24h; paid 30d default | Export/delete | Supabase Storage |
| Generation metadata and hashes | Ownership, support, duplicate abuse | Account/legal policy | Export metadata; hashes excluded | None |
| Payments and credit ledger | Billing and accounting | Legal/accounting term | Export; anonymized, not erased when retention required | Payment provider |
| Reduced security signals | Abuse and login protection | Configurable security window | Not exported in detail | Optional IP-risk vendor |
| Product analytics IDs/metadata | Funnel and reliability | Configurable | Account-associated events deleted/anonymized | None by default |

No passwords, raw card data, session JWTs, refresh tokens, image bytes, or internal secrets enter analytics/audit tables.

## Verification

```bash
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
pytest backend/tests
python scripts/check_migrations.py
python scripts/scan_frontend_secrets.py
```
