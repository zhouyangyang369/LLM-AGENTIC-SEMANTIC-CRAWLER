# -*- coding: utf-8 -*-
import os, sys, requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(r"C:/Users/1000302853/Desktop/Japan-university-entrance-examination-information-collection-website/web/nyushi-navi/.env.local")
SB_URL = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
SB_KEY = os.environ["NEXT_PUBLIC_SUPABASE_ANON_KEY"]
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}

# 実際のlab_urlサンプルを確認
r = requests.get(
    f"{SB_URL}/rest/v1/professor"
    f"?select=name_ja,university_name,lab_url"
    f"&lab_url=not.is.null"
    f"&limit=30",
    headers=H
)
data = r.json()
print(f"取得件数: {len(data)}")
print()
for d in data:
    print(f"{d['university_name']} / {d['name_ja']}")
    print(f"  {d['lab_url']}")