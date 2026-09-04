from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
MIGRATIONS="\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT/"supabase"/"migrations").glob("*.sql"))).lower()
SERVICES=(ROOT/"backend"/"app"/"services.py").read_text(encoding="utf-8")
WORKER=(ROOT/"backend"/"app"/"worker.py").read_text(encoding="utf-8")
FRONTEND="\n".join(p.read_text(encoding="utf-8",errors="ignore") for p in (ROOT/"frontend"/"src").rglob("*") if p.is_file())

def test_fal_key_never_appears_in_frontend_source():assert "FAL_KEY" not in FRONTEND
def test_fal_is_only_generation_provider():assert "check(provider='fal.ai')" in MIGRATIONS and "openai" not in SERVICES.lower()
def test_guest_limit_is_database_atomic():assert "for update" in MIGRATIONS and "free_generations_used=free_generations_used+1" in MIGRATIONS
def test_paid_credit_reservation_is_database_atomic():assert "reserve_paid_credit" in MIGRATIONS and "reserved_credits=reserved_credits+1" in MIGRATIONS
def test_duplicate_generation_is_idempotent():assert "generations_guest_idempotency_idx" in MIGRATIONS and "generations_user_idempotency_idx" in MIGRATIONS
def test_payment_webhook_is_idempotent():assert "payments_provider_payment_idx" in MIGRATIONS and "provider_event_id" in MIGRATIONS
def test_historical_prices_are_snapshotted():
    for field in ("package_name_snapshot","generation_count_snapshot","price_snapshot","currency_snapshot","discount_snapshot"):assert field in MIGRATIONS
def test_paid_ads_default_off_and_override_exists():assert "paid_ads_enabled','false'" in MIGRATIONS and "principal.is_paid:return none" in SERVICES.lower()
def test_failed_jobs_have_refund_paths():assert "refund_paid_credit" in WORKER and "refund_guest_generation" in WORKER
def test_worker_reconciliation_exists():assert "reconcile_stuck" in WORKER and "generation.recovered" in WORKER
def test_cross_user_data_has_rls():
    for table in ("profiles","generations","credit_wallets","credit_transactions","payments"):assert f"alter table {table} enable row level security" in MIGRATIONS
def test_guest_conversion_is_single_owner():assert "if g.converted_user_id is not null" in MIGRATIONS and "exactly_one_generation_owner" in MIGRATIONS
def test_admin_changes_are_auditable():assert "admin_audit_logs" in MIGRATIONS
def test_pricing_is_seeded_in_database_not_frontend():assert "single look" in MIGRATIONS and 'price:0.50' not in FRONTEND
def test_websocket_ownership_is_enforced():assert "owns_generation(principal,generation_id)" in (ROOT/"backend"/"app"/"main.py").read_text(encoding="utf-8")
def test_polling_fallback_exists():assert "setInterval(()=>void fetchStatus(),3000)" in FRONTEND
def test_payment_credits_require_verified_webhook():assert "verify_webhook_signature" in (ROOT/"backend"/"app"/"main.py").read_text(encoding="utf-8")
def test_signup_rate_limit_exists():assert "signup_rate_limit" in MIGRATIONS and "/api/auth/preflight/signup" in FRONTEND
def test_login_brute_force_cooldown_exists():assert "login-fail:" in (ROOT/"backend"/"app"/"main.py").read_text(encoding="utf-8")
def test_suspension_blocks_generation():assert 'account_status"] in ("suspended", "deleted")' in (ROOT/"backend"/"app"/"security.py").read_text(encoding="utf-8")
def test_pricing_updates_without_deployment():assert "upsert_credit_package" in MIGRATIONS and "/api/admin/pricing" in (ROOT/"backend"/"app"/"main.py").read_text(encoding="utf-8")
