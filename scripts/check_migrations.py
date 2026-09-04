import re
from pathlib import Path

root=Path(__file__).resolve().parents[1]
files=sorted((root/"supabase"/"migrations").glob("*.sql"))
assert files, "No migrations found"
numbers=[int(p.name.split("_")[0]) for p in files]
assert numbers==sorted(set(numbers)), "Migration versions must be unique and ordered"
sql="\n".join(p.read_text(encoding="utf-8") for p in files).lower()
required=["profiles","guest_sessions","user_sessions","generations","credit_wallets","credit_transactions","credit_packages","promotions","payments","payment_transactions","financial_events","ad_placements","ad_events","ad_revenue_reports","analytics_events","system_settings","security_events","admin_audit_logs"]
for table in required:
    assert re.search(rf"create table\s+{table}\b",sql),f"Missing table: {table}"
    assert f"alter table {table} enable row level security" in sql,f"RLS missing: {table}"
assert "paid_ads_enabled','false'" in sql,"Paid ads must default false"
assert "provider text not null default 'fal.ai' check(provider='fal.ai')" in sql,"Generation provider must be fal.ai only"
print(f"Validated {len(files)} migrations and {len(required)} required RLS tables.")

