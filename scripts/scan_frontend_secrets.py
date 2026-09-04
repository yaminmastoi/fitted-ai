from pathlib import Path

root=Path(__file__).resolve().parents[1]
blocked=("FAL_KEY","SUPABASE_SERVICE_ROLE_KEY","PAYMENT_SECRET","PAYMENT_WEBHOOK_SECRET")
hits=[]
for base in (root/"frontend"/"src",root/"frontend"/"dist"):
    if not base.exists():continue
    for path in base.rglob("*"):
        if path.is_file():
            text=path.read_text(encoding="utf-8",errors="ignore")
            hits.extend((path,key) for key in blocked if key in text)
assert not hits,"Backend secret names found in frontend: "+str(hits)
print("Frontend source and bundle contain no backend secret identifiers.")

