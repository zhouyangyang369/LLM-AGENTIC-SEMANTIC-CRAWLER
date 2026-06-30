# -*- coding: utf-8 -*-
import os, sys, requests, re
from dotenv import load_dotenv
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(r"C:/Users/1000302853/Desktop/Japan-university-entrance-examination-information-collection-website/web/nyushi-navi/.env.local")
SB_URL = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
SB_KEY = os.environ["NEXT_PUBLIC_SUPABASE_ANON_KEY"]
H = {
    "apikey": SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Prefer": "count=exact"
}

def get_count(params=""):
    r = requests.get(f"{SB_URL}/rest/v1/professor?select=id{params}&limit=1", headers=H)
    cr = r.headers.get("content-range", "0/0")
    return int(cr.split("/")[-1]) if "/" in cr else 0

def get_data(params="", limit=5000):
    r = requests.get(f"{SB_URL}/rest/v1/professor?select=id,name_ja,university_name,lab_url{params}&limit={limit}", headers=H)
    return r.json() if r.status_code == 200 else []

print("=" * 60)
print("  研究室URL 取得品質レポート")
print("=" * 60)

# 1. 基本統計
total   = get_count()
has_url = get_count("&lab_url=not.is.null")
no_url  = get_count("&lab_url=is.null")
rate    = round(has_url / total * 100, 1) if total > 0 else 0

print(f"\n【基本統計】")
print(f"  総レコード数     : {total:,} 件")
print(f"  lab_url あり     : {has_url:,} 件")
print(f"  lab_url なし     : {no_url:,} 件")
print(f"  取得率           : {rate} %")

# 2. 大学別の取得状況
print(f"\n【大学別 取得状況】")
univs = [
    "東京大学", "京都大学", "大阪大学",
    "名古屋大学", "東北大学", "北海道大学", "九州大学"
]
for univ in univs:
    enc = requests.utils.quote(univ)
    t  = get_count(f"&university_name=eq.{enc}")
    h  = get_count(f"&university_name=eq.{enc}&lab_url=not.is.null")
    r2 = round(h / t * 100, 1) if t > 0 else 0
    bar = "█" * int(r2 / 5) + "░" * (20 - int(r2 / 5))
    print(f"  {univ:8s} : {bar} {h:4d}/{t:4d} ({r2}%)")

# 3. URLドメイン品質分析
print(f"\n【URLドメイン品質分析】")
data = get_data("&lab_url=not.is.null", limit=5000)
urls = [d["lab_url"] for d in data if d.get("lab_url")]

ac_jp    = sum(1 for u in urls if ".ac.jp" in u)
univ_off = sum(1 for u in urls if any(d in u for d in [
    "u-tokyo.ac.jp", "kyoto-u.ac.jp", "osaka-u.ac.jp",
    "nagoya-u.ac.jp", "tohoku.ac.jp", "hokudai.ac.jp", "kyushu-u.ac.jp"
]))
pdf_urls = sum(1 for u in urls if u.endswith(".pdf"))
kdb_urls = sum(1 for u in urls if "kdb.iimc.kyoto-u.ac.jp" in u)
other_ac = ac_jp - univ_off

print(f"  大学公式ドメイン     : {univ_off:,} 件 ({round(univ_off/len(urls)*100,1) if urls else 0}%)")
print(f"  その他 .ac.jp        : {other_ac:,} 件 ({round(other_ac/len(urls)*100,1) if urls else 0}%)")
print(f"  .ac.jp 合計          : {ac_jp:,} 件 ({round(ac_jp/len(urls)*100,1) if urls else 0}%)")
print(f"  PDF ファイル         : {pdf_urls:,} 件 ({round(pdf_urls/len(urls)*100,1) if urls else 0}%) ← 要注意")
print(f"  京大KDBプロフィール  : {kdb_urls:,} 件 ({round(kdb_urls/len(urls)*100,1) if urls else 0}%)")

# 4. 問題URLのサンプル（PDF）
print(f"\n【問題URL サンプル（PDFリンク 上位10件）】")
pdf_samples = [d for d in data if d.get("lab_url", "").endswith(".pdf")][:10]
for d in pdf_samples:
    print(f"  {d['university_name']} / {d['name_ja']}")
    print(f"    → {d['lab_url']}")

# 5. 重複URL（同じURLが複数の教授に割り当てられているケース）
print(f"\n【重複URL 上位10件（品質問題の可能性）】")
url_counter = Counter(u for u in urls if u)
duplicates  = [(url, cnt) for url, cnt in url_counter.most_common(20) if cnt > 1]
for url, cnt in duplicates[:10]:
    print(f"  {cnt}件 → {url}")

print("\n" + "=" * 60)
print("  レポート終了")
print("=" * 60)