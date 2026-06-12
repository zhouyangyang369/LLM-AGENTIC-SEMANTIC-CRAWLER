# -*- coding: utf-8 -*-
"""
Phase 4 前置清洗 Step 1: 非募集要項文書・無関係ドメインの除外

処理内容:
  1. 非募集要項キーワードを含む文書に is_excluded=True を設定
  2. 無関係ドメイン（mext統計・県教委・広告サイト等）を除外
  3. covered_units=0 かつ 非募集要項URLパターンの文書を除外

使用方法:
  # 確認のみ（実際には変更しない）
  python scripts/phase4_step1_filter.py --dry-run

  # 実際に除外フラグを設定
  python scripts/phase4_step1_filter.py

  # 特定大学のみ
  python scripts/phase4_step1_filter.py --universities 北海道大学 東北大学

  # 除外済みをリセットして再実行
  python scripts/phase4_step1_filter.py --reset
"""
import sys
import os
import json
import argparse
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv()

# ── 除外ルール定義 ────────────────────────────────────────────────

# Rule 1: 非募集要項キーワード（notes や url に含まれる場合に除外候補）
NON_ADMISSION_URL_PATTERNS = [
    # 合格発表・成績関連
    'gokaku', 'goukaku', 'result', 'seiseki', 'hekka', 'kekka',
    'goukakusha', '合格者', '合格発表', '成績',
    # 学生便覧・シラバス
    'youran', 'handbook', 'syllabus', 'jikanwari',
    # 入学式・オープンキャンパス
    'nyugakushiki', 'opencampus', 'open-campus',
    # 中期計画・情報公開
    'chukikeikaku', 'johokokai',
    # 過去問
    'kakomon', 'kako', 'past',
]

NON_ADMISSION_CONTENT_KEYWORDS = [
    '合格者', '合格発表', '合格点', '合格最低点',
    '入学式', 'シラバス', '時間割', '学生便覧', '学生要覧',
    '中期計画', '情報公開', '年次報告', '紀要',
    '教員募集', '職員募集', '採用情報',
    # 明らかに無関係な英語文書
    'Tax strategy', 'tax strategy', 'NHS', 'hospital',
    'Blackpool', 'blackpool',
]

# Rule 2: 無関係ドメイン（完全除外）
EXCLUDED_DOMAINS = [
    # 広告・第三者サイト
    'shinken-ad.co.jp',       # 進研アド（広告）
    'benesse.ne.jp',          # ベネッセ
    'keinet.ne.jp',           # 河合塾
    'dnc.ac.jp',              # 大学入試センター（統計）
    'janu.jp',                # 大学入試センター関連
    # 英国NHS関連（誤爬取）
    'nhs.uk',
    'cqc.org.uk',
    'bbc.co.uk',
    'bbc.com',
    'blackpoolteachinghospitals.nhs.uk',
    'uk.a-hospital.com',
    # 県教委・地方機関（大学募集要項ではない）
    'pref.saitama.lg.jp',
    'pref.ibaraki.jp',
    'tochigi-edu.ed.jp',
    'pref.akita.lg.jp',
    'pref.iwate.jp',
    # 統計・政府機関（文科省統計ページ等）
    'ibconsortium.mext.go.jp',
    'gender.go.jp',
    'cao.go.jp',
    'kouseikyoku.mhlw.go.jp',
    # その他明らかに無関係
    'bnw-inc.jp',             # 不明な民間サイト
    'uploads.guim.co.uk',     # Guardian紙
    'jfm.go.jp',              # 地方公共団体金融機構（大学募集要項とは無関係）
]

# Rule 3: mext.go.jp は統計ページのみ除外（入試情報は保留）
MEXT_EXCLUDE_PATTERNS = [
    'mext_daigakuc',   # 大学入試統計
    'shinro',          # 進路関連統計
    '000038206',       # 特定の統計ファイル
]


def should_exclude(record: dict) -> tuple[bool, str]:
    """
    レコードを除外すべきか判定する。
    Returns: (除外すべきか, 除外理由)
    """
    pdf_url = record.get('pdf_url', '') or ''
    extracted_units = record.get('extracted_units') or {}

    if isinstance(extracted_units, str):
        try:
            extracted_units = json.loads(extracted_units)
        except Exception:
            extracted_units = {}

    # ── Rule 2: 完全除外ドメイン ──────────────────────────────
    try:
        domain = urlparse(pdf_url).netloc.lower()
        for excl_domain in EXCLUDED_DOMAINS:
            if excl_domain in domain:
                return True, f'除外ドメイン: {excl_domain}'
    except Exception:
        pass

    # ── Rule 3: mext.go.jp 統計ページ ────────────────────────
    if 'mext.go.jp' in pdf_url:
        for pattern in MEXT_EXCLUDE_PATTERNS:
            if pattern in pdf_url:
                return True, f'文科省統計ページ: {pattern}'

    # ── Rule 1: URL パターンチェック ─────────────────────────
    pdf_url_lower = pdf_url.lower()
    for pattern in NON_ADMISSION_URL_PATTERNS:
        if pattern in pdf_url_lower:
            # covered_units が存在する場合は慎重に（偽陽性回避）
            covered = extracted_units.get('covered_units', [])
            if not covered:  # covered_units なし → 確実に除外
                return True, f'非募集要項URLパターン（covered_units無し）: {pattern}'
            # covered_units ありでも「成績・合格者」は除外
            if pattern in ['seiseki', 'kekka', 'goukakusha', '合格者', '合格発表', '成績']:
                return True, f'合格発表・成績URL（covered_unitsあり）: {pattern}'

    # ── Rule 1: コンテンツキーワードチェック ─────────────────
    notes = str(extracted_units.get('notes', ''))
    covered_units_text = json.dumps(extracted_units.get('covered_units', []), ensure_ascii=False)
    full_text = notes + ' ' + covered_units_text + ' ' + pdf_url

    for kw in NON_ADMISSION_CONTENT_KEYWORDS:
        if kw in full_text:
            covered = extracted_units.get('covered_units', [])
            if not covered:  # covered_units なし → 除外
                return True, f'非募集要項キーワード（covered_units無し）: {kw}'
            # covered_units ありの場合は「明らかに無関係」のみ除外
            if kw in ['Tax strategy', 'tax strategy', 'NHS', 'hospital',
                      'Blackpool', 'blackpool', '入学式']:
                return True, f'明らかに無関係なコンテンツ: {kw}'

    return False, ''


def main():
    parser = argparse.ArgumentParser(description='Phase 4 Step 1: 非募集要項文書の除外フラグ設定')
    parser.add_argument('--dry-run', action='store_true', help='実際には変更せず確認のみ')
    parser.add_argument('--universities', nargs='+', help='対象大学名（未指定=全大学）')
    parser.add_argument('--reset', action='store_true', help='除外フラグをリセットして再実行')
    args = parser.parse_args()

    from src.db.supabase_client import get_supabase
    client = get_supabase()

    mode_str = '[DRY-RUN] ' if args.dry_run else ''
    print(f'{mode_str}Phase 4 Step 1: 非募集要項文書フィルタリング開始')
    print('=' * 65)

    # ── リセット処理 ─────────────────────────────────────────
    if args.reset and not args.dry_run:
        print('除外フラグをリセット中...')
        q = client.table('crawled_pdfs').update({
            'is_excluded': False,
            'exclusion_reason': None,
            'is_cleaned': False
        })
        if args.universities:
            q = q.in_('university_name', args.universities)
        q.execute()
        print('リセット完了\n')

    # ── データ取得 ────────────────────────────────────────────
    print('データ取得中...', file=sys.stderr)
    all_records = []
    page_size = 500
    offset = 0
    while True:
        q = client.table('crawled_pdfs')\
            .select('id,university_name,pdf_url,extracted_units,is_excluded')\
            .range(offset, offset + page_size - 1)
        if args.universities:
            q = q.in_('university_name', args.universities)
        r = q.execute()
        if not r.data:
            break
        all_records.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size

    print(f'対象レコード: {len(all_records)} 件\n')

    # ── 判定 ─────────────────────────────────────────────────
    to_exclude = []   # (id, reason)
    already_excluded = 0
    keep_count = 0

    for rec in all_records:
        if rec.get('is_excluded') and not args.reset:
            already_excluded += 1
            continue

        exclude, reason = should_exclude(rec)
        if exclude:
            to_exclude.append((rec['id'], rec['university_name'], rec.get('pdf_url', '')[:80], reason))
        else:
            keep_count += 1

    # ── 結果表示 ──────────────────────────────────────────────
    print(f'判定結果:')
    print(f'  除外対象:     {len(to_exclude):>5} 件')
    print(f'  保持:         {keep_count:>5} 件')
    print(f'  処理済みスキップ: {already_excluded:>3} 件')
    print()

    # 除外理由の集計
    from collections import Counter
    reason_counter = Counter(reason for _, _, _, reason in to_exclude)
    print('除外理由の内訳:')
    for reason, cnt in reason_counter.most_common():
        print(f'  {reason:<50} {cnt:>4} 件')
    print()

    # 除外対象の詳細（最大30件表示）
    print(f'除外対象サンプル（最大30件）:')
    for rec_id, univ, url, reason in to_exclude[:30]:
        print(f'  [{univ}] {reason}')
        print(f'    {url}')

    if args.dry_run:
        print(f'\n[DRY-RUN] 実際の変更は行いません。--dry-run を外して実行してください。')
        return

    # ── 実際の更新 ────────────────────────────────────────────
    if not to_exclude:
        print('除外対象がありません。')
    else:
        print(f'\n{len(to_exclude)} 件に除外フラグを設定中...')
        batch_size = 50
        updated = 0
        for i in range(0, len(to_exclude), batch_size):
            batch = to_exclude[i:i + batch_size]
            for rec_id, _, _, reason in batch:
                client.table('crawled_pdfs').update({
                    'is_excluded': True,
                    'exclusion_reason': reason,
                    'is_cleaned': True
                }).eq('id', rec_id).execute()
                updated += 1
            print(f'  更新済み: {updated}/{len(to_exclude)} 件...', file=sys.stderr)

        print(f'\n✅ 完了: {updated} 件に is_excluded=True を設定しました。')

    # 保持レコードにも is_cleaned=True を設定
    print('保持レコードに is_cleaned=True を設定中...')
    keep_ids = [
        rec['id'] for rec in all_records
        if not any(rec['id'] == eid for eid, _, _, _ in to_exclude)
        and not rec.get('is_excluded')
    ]
    for i in range(0, len(keep_ids), 100):
        batch_ids = keep_ids[i:i + 100]
        client.table('crawled_pdfs').update({'is_cleaned': True})\
            .in_('id', batch_ids).execute()
    print(f'✅ {len(keep_ids)} 件に is_cleaned=True を設定しました。')


if __name__ == '__main__':
    main()