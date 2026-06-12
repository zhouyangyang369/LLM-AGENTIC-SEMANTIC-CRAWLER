# -*- coding: utf-8 -*-
"""
Phase 4 前置清洗 Step 2: academic_year 修正

処理内容:
  1. extracted_units 内の実際の年度を読み取り actual_year フィールドに保存
  2. academic_year を実際の年度で上書き
  3. 年度の正規化（「2025年度」→「令和7年度」等）

使用方法:
  python scripts/phase4_step2_fix_year.py --dry-run
  python scripts/phase4_step2_fix_year.py
  python scripts/phase4_step2_fix_year.py --universities 北海道大学
"""
import sys
import os
import json
import re
import argparse

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv()

# 西暦 → 和暦変換マップ
SEIREKI_TO_WAREKI = {
    '2019': '令和元年度', '2020': '令和2年度', '2021': '令和3年度',
    '2022': '令和4年度', '2023': '令和5年度', '2024': '令和6年度',
    '2025': '令和7年度', '2026': '令和8年度', '2027': '令和9年度',
    '2028': '令和10年度', '2029': '令和11年度', '2030': '令和12年度',
}

# 正規化パターン（よくある表記ゆれ）
NORMALIZE_PATTERNS = [
    # 「令和8年度（2026年度）」→ 「令和8年度」
    (r'(令和\d+年度)（\d+年度）', r'\1'),
    (r'(令和\d+年度)\(\d+年度\)', r'\1'),
    # 「2026年度」→ 「令和8年度」（後で変換）
    (r'^(\d{4})年度$', 'seireki'),
    # 「R7」「R08」→ 「令和7年度」
    (r'^[Rr](\d{1,2})$', 'reiwa_short'),
    (r'^[Rr](\d{1,2})年度$', 'reiwa_short_nendo'),
    # 「令和7」（「年度」なし）→ 「令和7年度」
    (r'^(令和\d+)$', r'\1年度'),
    # 「2026年10月入学」→ 「令和8年度」
    (r'^(\d{4})年\d+月入学$', 'seireki_month'),
]


def normalize_year(raw_year: str) -> str:
    """年度表記を正規化して返す"""
    if not raw_year:
        return ''

    year = str(raw_year).strip()

    # 既に正規化済み
    if re.match(r'^令和\d+年度$', year):
        return year
    if re.match(r'^平成\d+年度$', year):
        return year  # 平成はそのまま保持

    # 西暦年度変換
    m = re.match(r'^(\d{4})年度$', year)
    if m:
        seireki = m.group(1)
        return SEIREKI_TO_WAREKI.get(seireki, year)

    # 西暦+月入学
    m = re.match(r'^(\d{4})年\d+月入学$', year)
    if m:
        seireki = m.group(1)
        return SEIREKI_TO_WAREKI.get(seireki, year)

    # R7 形式
    m = re.match(r'^[Rr](\d{1,2})$', year)
    if m:
        return f'令和{m.group(1)}年度'
    m = re.match(r'^[Rr](\d{1,2})年度$', year)
    if m:
        return f'令和{m.group(1)}年度'

    # 「令和8年度（2026年度）」の括弧除去
    m = re.match(r'(令和\d+年度)[（(].*[）)]', year)
    if m:
        return m.group(1)

    # 「令和7」（年度なし）
    m = re.match(r'^(令和\d+)$', year)
    if m:
        return m.group(1) + '年度'

    return year  # 変換できない場合はそのまま


def extract_year_from_units(extracted_units: dict) -> str:
    """extracted_units から実際の年度を抽出する"""
    if not extracted_units:
        return ''

    # 直接フィールド
    for key in ['academic_year', 'year', 'nendo']:
        val = extracted_units.get(key, '')
        if val and val not in ('不明', 'unknown', ''):
            return normalize_year(str(val))

    # covered_units 内を探索
    covered = extracted_units.get('covered_units', [])
    if isinstance(covered, list):
        for unit in covered:
            if isinstance(unit, dict):
                for key in ['academic_year', 'year']:
                    val = unit.get(key, '')
                    if val and val not in ('不明', 'unknown', ''):
                        return normalize_year(str(val))

    return ''


def main():
    parser = argparse.ArgumentParser(description='Phase 4 Step 2: academic_year 修正')
    parser.add_argument('--dry-run', action='store_true', help='確認のみ（変更なし）')
    parser.add_argument('--universities', nargs='+', help='対象大学名（未指定=全大学）')
    args = parser.parse_args()

    from src.db.supabase_client import get_supabase
    client = get_supabase()

    mode_str = '[DRY-RUN] ' if args.dry_run else ''
    print(f'{mode_str}Phase 4 Step 2: academic_year 修正開始')
    print('=' * 65)

    # ── データ取得 ────────────────────────────────────────────
    print('データ取得中...', file=sys.stderr)
    all_records = []
    page_size = 500
    offset = 0
    while True:
        q = client.table('crawled_pdfs')\
            .select('id,university_name,academic_year,extracted_units')\
            .eq('is_excluded', False)\
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

    print(f'対象レコード（除外済みを除く）: {len(all_records)} 件\n')

    # ── 年度抽出・修正判定 ────────────────────────────────────
    from collections import Counter
    updates = []  # (id, actual_year, new_academic_year)
    year_distribution = Counter()
    no_year_found = 0

    for rec in all_records:
        eu = rec.get('extracted_units') or {}
        if isinstance(eu, str):
            try:
                eu = json.loads(eu)
            except Exception:
                eu = {}

        actual_year = extract_year_from_units(eu)
        current_academic_year = rec.get('academic_year', '')

        if actual_year:
            year_distribution[actual_year] += 1
            updates.append((rec['id'], actual_year, actual_year))
        else:
            no_year_found += 1
            year_distribution['不明'] += 1
            # actual_year なしの場合は academic_year をそのまま保持
            updates.append((rec['id'], None, current_academic_year))

    # ── 結果表示 ──────────────────────────────────────────────
    print('抽出された年度分布:')
    for year, cnt in sorted(year_distribution.items(),
                            key=lambda x: -x[1]):
        flag = ''
        if year not in ('令和7年度', '令和8年度', '令和9年度', '不明'):
            if '平成' in year or year in ('令和3年度', '令和4年度', '令和5年度', '令和6年度'):
                flag = '  ← 旧文書'
        print(f'  {year:<25} {cnt:>5} 件{flag}')

    print(f'\n  年度取得できず: {no_year_found} 件')
    print(f'  年度修正対象:   {len([u for u in updates if u[1]]):} 件')

    if args.dry_run:
        print(f'\n[DRY-RUN] 実際の変更は行いません。')
        return

    # ── 実際の更新 ────────────────────────────────────────────
    print(f'\nacademic_year / actual_year を更新中...')
    updated = 0
    for rec_id, actual_year, new_academic_year in updates:
        update_data = {}
        if actual_year:
            update_data['actual_year'] = actual_year
            update_data['academic_year'] = new_academic_year
        client.table('crawled_pdfs').update(update_data)\
            .eq('id', rec_id).execute()
        updated += 1
        if updated % 100 == 0:
            print(f'  更新済み: {updated}/{len(updates)} 件...', file=sys.stderr)

    print(f'\n✅ 完了: {updated} 件の academic_year / actual_year を更新しました。')


if __name__ == '__main__':
    main()