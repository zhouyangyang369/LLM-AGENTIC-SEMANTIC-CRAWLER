# -*- coding: utf-8 -*-
"""
研究室URL取得スクリプト
戦略: DuckDuckGo で「大学名 教授名 研究分野 研究室」を検索し
      大学公式ドメインのURLを研究室HPとして保存する
"""
import os, sys, time, re, logging, requests
from dotenv import load_dotenv
from duckduckgo_search import DDGS

sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('crawl_lab_urls.log', encoding='utf-8'),
    ]
)
log = logging.getLogger(__name__)

load_dotenv(r'C:/Users/1000302853/Desktop/Japan-university-entrance-examination-information-collection-website/web/nyushi-navi/.env.local')
URL = os.environ['NEXT_PUBLIC_SUPABASE_URL']
KEY = os.environ['NEXT_PUBLIC_SUPABASE_ANON_KEY']
H   = {'apikey': KEY, 'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json'}

# 大学ごとの公式ドメイン
UNIV_DOMAINS = {
    '東京大学':   ['u-tokyo.ac.jp'],
    '京都大学':   ['kyoto-u.ac.jp'],
    '大阪大学':   ['osaka-u.ac.jp'],
    '名古屋大学': ['nagoya-u.ac.jp'],
    '東北大学':   ['tohoku.ac.jp'],
    '北海道大学': ['hokudai.ac.jp'],
    '九州大学':   ['kyushu-u.ac.jp'],
}

# 除外パターン（研究室HPらしくないURL）
EXCLUDE_PATTERNS = [
    r'researchmap\.jp',
    r'kaken\.nii\.ac\.jp',
    r'jglobal\.jst\.go\.jp',
    r'scholar\.google',
    r'ci\.nii\.ac\.jp',
    r'wikipedia',
    r'linkedin',
    r'twitter',
    r'facebook',
    r'/news/',
    r'/topics/',
    r'/events/',
    r'recruit',
    r'nyushi',
    r'admission',
]

def is_lab_url(url: str, univ: str) -> bool:
    """URLが研究室HPらしいか判定"""
    domains = UNIV_DOMAINS.get(univ, [])
    # 大学ドメインでない場合はスキップ
    if not any(d in url for d in domains):
        return False
    # 除外パターンに一致する場合はスキップ
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, url):
            return False
    # 研究室らしいキーワードが含まれているとポイント高い
    lab_keywords = ['lab', 'labo', 'laboratory', 'research', 'prof', 'faculty',
                    '研究室', '研究グループ', 'group']
    url_lower = url.lower()
    bonus = any(kw in url_lower for kw in lab_keywords)
    return True

def search_lab_url(name_ja: str, univ: str, fields: list, keywords: list) -> str | None:
    """DuckDuckGoで研究室URLを検索"""
    # フィールド・キーワードから検索クエリを構築
    field_str = ' '.join(fields[:2]) if fields else ''
    kw_str    = ' '.join(keywords[:2]) if keywords else ''
    
    queries = [
        f'{univ} {name_ja} 研究室',
        f'{univ} {name_ja} {field_str} 研究室'.strip(),
        f'{univ} {name_ja} lab',
    ]
    
    domains = UNIV_DOMAINS.get(univ, [])
    
    with DDGS() as ddgs:
        for query in queries:
            try:
                results = list(ddgs.text(query, max_results=10, region='jp-jp'))
                time.sleep(1.5)  # レート制限対策
                
                # 大学ドメインのURLを優先
                for r in results:
                    href = r.get('href', '')
                    if is_lab_url(href, univ):
                        log.info(f'  ✓ {name_ja}: {href}')
                        return href
                
                # 大学ドメインで見つからなければ次のクエリへ
            except Exception as e:
                log.warning(f'  DDG error for {name_ja}: {e}')
                time.sleep(5)
                continue
    
    return None

def get_professors_without_lab_url(limit: int = 5000) -> list:
    """lab_urlがNULLの教授を取得"""
    r = requests.get(
        f'{URL}/rest/v1/professor'
        f'?select=id,name_ja,university_name,research_fields,keywords'
        f'&lab_url=is.null'
        f'&name_ja=not.ilike.*Access*'  # アクセス集中エラーを除外
        f'&order=university_name,id'
        f'&limit={limit}',
        headers=H
    )
    if r.status_code == 200:
        return r.json()
    log.error(f'取得エラー: {r.status_code} {r.text[:200]}')
    return []

def update_lab_url(prof_id: int, lab_url: str) -> bool:
    """lab_urlをSupabaseに保存"""
    r = requests.patch(
        f'{URL}/rest/v1/professor?id=eq.{prof_id}',
        headers=H,
        json={'lab_url': lab_url}
    )
    return r.status_code in (200, 204)

def main():
    log.info('=== 研究室URL取得クローラー 開始 ===')
    
    # まずlab_urlカラムが存在するか確認
    r = requests.get(
        f'{URL}/rest/v1/professor?select=lab_url&limit=1',
        headers=H
    )
    if r.status_code != 200:
        log.error('lab_urlカラムが存在しません。Supabaseで先にカラムを追加してください。')
        log.error('SQL: ALTER TABLE professor ADD COLUMN IF NOT EXISTS lab_url TEXT;')
        return
    
    log.info('lab_urlカラム確認OK')
    
    profs = get_professors_without_lab_url(limit=5000)
    log.info(f'対象教員数: {len(profs)}件')
    
    success = 0
    not_found = 0
    
    for i, prof in enumerate(profs):
        pid        = prof['id']
        name       = prof['name_ja']
        univ       = prof['university_name']
        fields     = prof.get('research_fields') or []
        keywords   = prof.get('keywords') or []
        
        # 対象大学のみ処理
        if univ not in UNIV_DOMAINS:
            continue
        
        log.info(f'[{i+1}/{len(profs)}] {univ} / {name}')
        
        lab_url = search_lab_url(name, univ, fields, keywords)
        
        if lab_url:
            ok = update_lab_url(pid, lab_url)
            if ok:
                success += 1
                log.info(f'  保存OK: {lab_url}')
            else:
                log.warning(f'  保存FAILED')
        else:
            not_found += 1
            log.info(f'  見つからず')
        
        # 進捗ログ（100件ごと）
        if (i + 1) % 100 == 0:
            log.info(f'--- 進捗: {i+1}件処理済み / 成功:{success} / 未発見:{not_found} ---')
        
        # DDGレート制限対策
        time.sleep(2)
    
    log.info(f'=== 完了 ===')
    log.info(f'成功: {success}件 / 未発見: {not_found}件')

if __name__ == '__main__':
    main()
