# -*- coding: utf-8 -*-
"""
researchmap 旧帝大7校 教員情報クローラー（本番版）

実行方法: python scripts/crawl_professors.py
"""
import sys, os, time, re, json, requests, logging
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

load_dotenv(r'C:/Users/1000302853/Desktop/Japan-university-entrance-examination-information-collection-website/web/nyushi-navi/.env.local')
SUPABASE_URL = os.environ['NEXT_PUBLIC_SUPABASE_URL']
SUPABASE_KEY = os.environ['NEXT_PUBLIC_SUPABASE_ANON_KEY']

TARGET_UNIVERSITIES = [
    '東京大学',
    '京都大学',
    '大阪大学',
    '名古屋大学',
    '東北大学',
    '北海道大学',
    '九州大学',
]

TARGET_POSITIONS = {
    '教授', '准教授', '助教', '講師',
    '特任教授', '特任准教授', '特任講師', '特任助教',
    '客員教授', '客員准教授', '招へい教授', '招へい准教授',
}

SECTION_HEADERS = {
    '研究分野', '経歴', '主要な経歴', '学歴', '受賞', '論文',
    'MISC', '競争的資金', '書籍', '主要な学歴', '主要な委員歴'
}


def supabase_upsert(records):
    url = f'{SUPABASE_URL}/rest/v1/professor?on_conflict=researchmap_id'
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'resolution=merge-duplicates,return=minimal',
    }
    try:
        r = requests.post(url, headers=headers, json=records, timeout=30)
        if r.status_code in (200, 201):
            return True
        log.error(f'Supabase error: {r.status_code} {r.text[:200]}')
        return False
    except Exception as e:
        log.error(f'Supabase exception: {e}')
        return False


def get_existing_ids():
    url = f'{SUPABASE_URL}/rest/v1/professor?select=researchmap_id&limit=50000'
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
    }
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            return {row['researchmap_id'] for row in r.json() if row.get('researchmap_id')}
    except Exception as e:
        log.error(f'get_existing_ids error: {e}')
    return set()


def create_driver():
    options = Options()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--lang=ja')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def collect_urls(driver, university, max_pages=200):
    all_urls = []
    seen = set()
    consecutive_errors = 0
    for page in range(1, max_pages + 1):
        search_url = 'https://researchmap.jp/researchers?q=' + requests.utils.quote(university) + '&page=' + str(page)
        log.info(f'  検索ページ {page}: {search_url}')
        try:
            driver.set_page_load_timeout(30)
            driver.get(search_url)
            time.sleep(5)
            body_text = driver.find_element(By.TAG_NAME, 'body').text
            # 403チェック
            if '403' in body_text and 'Forbidden' in body_text:
                log.warning(f'  page {page}: 403 Forbidden - 60秒待機')
                time.sleep(60)
                driver.get(search_url)
                time.sleep(5)
                body_text = driver.find_element(By.TAG_NAME, 'body').text
            if page == 1:
                total = re.search(r'総件数\s*([\d,]+)', body_text)
                if total:
                    log.info(f'  総件数: {total.group(1)}件')
            page_count = 0
            links = driver.find_elements(By.TAG_NAME, 'a')
            for link in links:
                try:
                    href = link.get_attribute('href') or ''
                    text = link.text.strip()
                    if (re.match(r'https://researchmap\.jp/[a-zA-Z0-9_]+$', href)
                            and href not in seen
                            and text
                            and '/researchers' not in href
                            and '/new_accounts' not in href
                            and '/auth' not in href):
                        seen.add(href)
                        all_urls.append(href)
                        page_count += 1
                except Exception:
                    continue
            log.info(f'  page {page}: +{page_count}件 (累計: {len(all_urls)})')
            if page_count == 0:
                log.info('  新URLなし -> 終了')
                break
            consecutive_errors = 0
            time.sleep(2)
        except Exception as e:
            consecutive_errors += 1
            log.error(f'  page {page} エラー: {e}')
            if consecutive_errors >= 3:
                log.error('  3回連続エラー -> 終了')
                break
            time.sleep(10)
            continue
    return all_urls


def extract_data(html, url, university):
    soup = BeautifulSoup(html, 'html.parser')
    body_text = soup.get_text(separator='\n', strip=True)
    lines = [l.strip() for l in body_text.split('\n') if l.strip()]
    rm_id = url.rstrip('/').split('/')[-1]

    # 403チェック
    if '403 Forbidden' in body_text or len(lines) < 5:
        return None

    # 氏名
    name_ja = ''
    h1 = soup.find('h1')
    if h1:
        name_ja = h1.get_text(strip=True)
    if not name_ja or name_ja in ('研究者検索', 'researchmap'):
        return None

    # カナ・英語名
    name_en, name_kana = '', ''
    for i, line in enumerate(lines):
        if line == name_ja and i + 1 < len(lines):
            next_line = lines[i+1]
            kana_en = re.match(r'^([\u30A0-\u30FF\s\u30FB]+?)\s{2,}\((.+?)\)$', next_line)
            if kana_en:
                name_kana = kana_en.group(1).strip()
                name_en = kana_en.group(2).strip()
            elif re.match(r'^[\u30A0-\u30FF\s\u30FB]+$', next_line):
                name_kana = next_line
            elif re.match(r'^[A-Za-z\s\.\-]+$', next_line):
                name_en = next_line
            break

    # 所属・職位
    affiliation, position = '', ''
    for i, line in enumerate(lines):
        if line == '所属' and i + 1 < len(lines):
            affil_lines = []
            for j in range(i+1, min(i+10, len(lines))):
                if lines[j] in ('学位', '研究者番号', 'J-GLOBAL ID', 'researchmap会員ID', '外部リンク'):
                    break
                affil_lines.append(lines[j])
            affiliation = ' '.join(affil_lines)[:400]
            for pos in sorted(TARGET_POSITIONS, key=len, reverse=True):
                if pos in affiliation:
                    position = pos
                    break
            break

    # 研究科・専攻
    kenkyuka_name, senkou_name = '', ''
    if affiliation:
        km = re.search(r'([\S]+研究科)', affiliation)
        if km: kenkyuka_name = km.group(1)
        sm = re.search(r'([\S]+専攻)', affiliation)
        if sm: senkou_name = sm.group(1)

    # キーワード（2回目の出現を使用）
    keywords = []
    kw_occurrences = [i for i, line in enumerate(lines) if line == '研究キーワード']
    kw_start = kw_occurrences[1] if len(kw_occurrences) >= 2 else (kw_occurrences[0] if kw_occurrences else -1)
    if kw_start >= 0:
        j = kw_start + 1
        if j < len(lines) and re.match(r'^\d+$', lines[j]):
            j += 1
        while j < len(lines) and lines[j] not in SECTION_HEADERS:
            kw = lines[j].strip()
            if kw and not re.match(r'^\d+$', kw) and 1 < len(kw) < 50:
                keywords.append(kw)
            j += 1

    # 研究分野（2回目の出現を使用）
    research_fields = []
    rf_occurrences = [i for i, line in enumerate(lines) if line == '研究分野']
    rf_start = rf_occurrences[1] if len(rf_occurrences) >= 2 else (rf_occurrences[0] if rf_occurrences else -1)
    if rf_start >= 0:
        j = rf_start + 1
        if j < len(lines) and re.match(r'^\d+$', lines[j]):
            j += 1
        stop = SECTION_HEADERS | {'主要な経歴', '主要な学歴', '主要な委員歴'}
        while j < len(lines) and lines[j] not in stop:
            fl = lines[j].strip()
            if '/' in fl:
                parts = [p.strip() for p in fl.split('/') if p.strip() and 1 < len(p.strip()) < 40]
                research_fields.extend(parts)
            j += 1
        research_fields = list(dict.fromkeys(research_fields))[:10]

    # 更新日
    profile_updated = ''
    upd = re.search(r'更新日[:\uff1a]?\s*([\d/]+)', body_text)
    if upd: profile_updated = upd.group(1)

    return {
        'researchmap_id':  rm_id,
        'university_name': university,
        'affiliation':     affiliation,
        'kenkyuka_name':   kenkyuka_name,
        'senkou_name':     senkou_name,
        'name_ja':         name_ja,
        'name_en':         name_en,
        'name_kana':       name_kana,
        'position':        position,
        'research_fields': research_fields,
        'keywords':        keywords,
        'researchmap_url': url,
        'profile_updated': profile_updated,
        'updated_at':      datetime.utcnow().isoformat(),
    }


def main():
    # ログファイル
    fh = logging.FileHandler('crawl_professors.log', encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    log.addHandler(fh)

    log.info('=== 旧帝大7校 教員情報クローラー 開始 ===')
    existing_ids = get_existing_ids()
    log.info(f'既存レコード数: {len(existing_ids)}')

    driver = create_driver()
    total_saved = 0
    BATCH_SIZE = 30

    try:
        for university in TARGET_UNIVERSITIES:
            log.info(f'\n{"="*50}')
            log.info(f'大学: {university}')
            log.info(f'{"="*50}')

            urls = collect_urls(driver, university, max_pages=200)
            log.info(f'収集URL: {len(urls)}件')

            batch = []
            univ_saved = 0

            for i, url in enumerate(urls):
                rm_id = url.rstrip('/').split('/')[-1]
                if rm_id in existing_ids:
                    continue

                # 100件ごとにdriverを再起動（メモリリーク防止）
                if univ_saved > 0 and univ_saved % 200 == 0:
                    log.info('  Driver再起動中...')
                    driver.quit()
                    time.sleep(3)
                    driver = create_driver()

                log.info(f'[{university}][{i+1}/{len(urls)}] {url}')
                retry = 0
                while retry < 3:
                    try:
                        driver.set_page_load_timeout(30)
                        driver.get(url)
                        time.sleep(4)
                        html = driver.page_source
                        # 403チェック
                        if '403 Forbidden' in html:
                            log.warning(f'  403 -> 30秒待機後リトライ')
                            time.sleep(30)
                            retry += 1
                            continue
                        data = extract_data(html, url, university)
                        if data and data['researchmap_id'] != 'researchers':
                            batch.append(data)
                            existing_ids.add(rm_id)
                            univ_saved += 1
                            log.info(f'  OK: {data["name_ja"]} / {data["position"]} / {data["kenkyuka_name"]}')
                            if len(batch) >= BATCH_SIZE:
                                ok = supabase_upsert(batch)
                                total_saved += len(batch)
                                log.info(f'  バッチ保存: {len(batch)}件 (累計: {total_saved}) {"OK" if ok else "FAILED"}')
                                batch = []
                        else:
                            log.warning(f'  データなし')
                        time.sleep(1)
                        break
                    except Exception as e:
                        retry += 1
                        log.error(f'  エラー(retry {retry}): {e}')
                        time.sleep(5)

            if batch:
                ok = supabase_upsert(batch)
                total_saved += len(batch)
                log.info(f'[{university}] 残バッチ保存: {len(batch)}件 OK={ok}')
                batch = []

            log.info(f'[{university}] 完了: {univ_saved}件保存')

    finally:
        driver.quit()
        log.info(f'\n=== クローラー終了 ===')
        log.info(f'総保存件数: {total_saved}')


if __name__ == '__main__':
    main()
