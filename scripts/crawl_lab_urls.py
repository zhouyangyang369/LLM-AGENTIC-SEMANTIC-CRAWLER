# -*- coding: utf-8 -*-
import os, sys, time, re, logging, requests
from dotenv import load_dotenv
from ddgs import DDGS

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(r"C:/Users/1000302853/Desktop/Japan-university-entrance-examination-information-collection-website/web/nyushi-navi/.env.local")
SB_URL = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
SB_KEY = os.environ["NEXT_PUBLIC_SUPABASE_ANON_KEY"]
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("crawl_lab_urls.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

UNIV_DOMAINS = {
    "東京大学":   ["u-tokyo.ac.jp"],
    "京都大学":   ["kyoto-u.ac.jp"],
    "大阪大学":   ["osaka-u.ac.jp"],
    "名古屋大学": ["nagoya-u.ac.jp"],
    "東北大学":   ["tohoku.ac.jp"],
    "北海道大学": ["hokudai.ac.jp"],
    "九州大学":   ["kyushu-u.ac.jp"],
}

EXCLUDE_PATTERNS = [
    r"researchmap\.jp", r"kaken\.nii\.ac\.jp", r"jglobal\.jst\.go\.jp",
    r"scholar\.google", r"ci\.nii\.ac\.jp", r"wikipedia",
    r"linkedin", r"twitter\.com", r"facebook\.com",
    r"top-researchers\.com", r"lab-search\.com",
    r"dnc\.ac\.jp", r"benesse", r"mynavi", r"rikunabi", r"indeed\.com",
]

def score_url(url, univ):
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, url, re.I):
            return 0
    score = 0
    url_lower = url.lower()
    if any(d in url for d in UNIV_DOMAINS.get(univ, [])):
        score += 10
    if any(kw in url_lower for kw in ["lab","labo","laboratory","research","group","prof","faculty"]):
        score += 5
    if ".ac.jp" in url:
        score += 3
    if url.count("/") >= 4:
        score += 2
    return score

def search_lab_url(name_ja, univ, fields, keywords):
    field_str = " ".join((fields or [])[:2])
    queries = [
        f"{univ} {name_ja} 研究室",
        f"{univ} {name_ja} lab",
        f"{name_ja} {univ} {field_str} 研究室".strip(),
    ]
    best_url, best_score = None, 0
    ddgs = DDGS()
    for query in queries:
        try:
            results = ddgs.text(query, max_results=10)
            time.sleep(1.5)
            for r in results:
                href = r.get("href", "")
                s = score_url(href, univ)
                if s > best_score:
                    best_score = s
                    best_url = href
            if best_score >= 10:
                break
        except Exception as e:
            log.warning(f"  DDG error: {e}")
            time.sleep(5)
    return best_url if best_score >= 3 else None

def get_professors(limit=5000):
    r = requests.get(
        f"{SB_URL}/rest/v1/professor"
        f"?select=id,name_ja,university_name,research_fields,keywords"
        f"&lab_url=is.null"
        f"&name_ja=not.ilike.*Access*"
        f"&name_ja=not.ilike.*アクセス*"
        f"&order=university_name,id"
        f"&limit={limit}",
        headers=H
    )
    return r.json() if r.status_code == 200 else []

def update_lab_url(prof_id, lab_url):
    r = requests.patch(
        f"{SB_URL}/rest/v1/professor?id=eq.{prof_id}",
        headers=H, json={"lab_url": lab_url}
    )
    return r.status_code in (200, 204)

def main():
    log.info("=== 研究室URL取得クローラー 開始 ===")
    profs = get_professors()
    log.info(f"対象教員数: {len(profs)}件")
    success = not_found = errors = 0
    for i, prof in enumerate(profs):
        pid, name, univ = prof["id"], prof["name_ja"], prof["university_name"]
        if univ not in UNIV_DOMAINS:
            continue
        if "Access" in name or "アクセス" in name:
            continue
        log.info(f"[{i+1}/{len(profs)}] {univ} / {name}")
        try:
            lab_url = search_lab_url(name, univ, prof.get("research_fields") or [], prof.get("keywords") or [])
            if lab_url:
                if update_lab_url(pid, lab_url):
                    success += 1
                    log.info(f"  OK: {lab_url}")
                else:
                    errors += 1
            else:
                not_found += 1
                log.info(f"  - 見つからず")
        except Exception as e:
            errors += 1
            log.error(f"  エラー: {e}")
            time.sleep(10)
        if (i + 1) % 50 == 0:
            log.info(f"=== 進捗 {i+1}件 / 成功:{success} 未発見:{not_found} エラー:{errors} ===")
        time.sleep(2)
    log.info(f"=== 完了 === 成功:{success} / 未発見:{not_found} / エラー:{errors}")

if __name__ == "__main__":
    main()
