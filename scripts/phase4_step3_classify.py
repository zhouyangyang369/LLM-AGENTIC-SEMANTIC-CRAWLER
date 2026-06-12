# -*- coding: utf-8 -*-
"""
Phase 4 前置清洗 Step 3: doc_type / is_scan_pdf / exam_types 分類タグ付与

処理内容:
  1. is_scan_pdf フラグ設定（文字数 < 500 のPDF）
  2. doc_type タグ付与（ルールベース優先 → LLM補完）
  3. exam_types タグ付与（入試方式の検出）

使用方法:
  python scripts/phase4_step3_classify.py --dry-run
  python scripts/phase4_step3_classify.py
  python scripts/phase4_step3_classify.py --llm    # LLM分類も使用（コスト増）
  python scripts/phase4_step3_classify.py --universities 北海道大学
"""
import sys
import os
import json
import re
import argparse
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv()

# ── doc_type 判定ルール ───────────────────────────────────────────

# URL・notes から doc_type を推定するキーワード
DOC_TYPE_RULES = {
    '募集要項': [
        'boshu', 'youkou', '募集要項', '募集要領', 'boshuyoko',
        'nyushi_youkou', 'nyugaku_youkou',
    ],
    '選抜要項': [
        'senbatsu', '選抜要項', '選抜要領', '入学者選抜',
        'nyuugakusha_senbatsu',
    ],
    '出願要領': [
        'shutsugan', '出願要領', '出願案内', '出願手続',
        'application_guide',
    ],
    '入学案内': [
        'nyugaku_annai', '入学案内', 'nyuugaku_annai', 'guide',
        'annai', 'campus_guide',
    ],
    '合格発表': [
        'gokaku', 'goukaku', 'kekka', 'result', '合格発表', '合格者',
        '合格最低点', '合格点',
    ],
    '学生便覧': [
        'youran', 'handbook', '便覧', '要覧', 'student_guide',
    ],
    '成績': [
        'seiseki', 'score', '成績', '試験結果',
    ],
}

# ── exam_types 検出ルール ─────────────────────────────────────────
EXAM_TYPE_PATTERNS = {
    '一般選抜':       ['一般選抜', '一般入試', 'ippan', '前期日程', '後期日程', '中期日程'],
    '前期日程':       ['前期日程', 'zenki'],
    '後期日程':       ['後期日程', 'koki'],
    '学校推薦型選抜': ['学校推薦', '推薦入試', 'suisen', '指定校推薦', '公募推薦'],
    '総合型選抜':     ['総合型', 'AO入試', 'AO選抜', 'sogo', '自己推薦'],
    '社会人入試':     ['社会人', 'shakaijin'],
    '外国人留学生':   ['外国人', '留学生', 'ryugakusei', '外国語'],
    '編入学':         ['編入', 'hennyu', '転入'],
    '大学院':         ['大学院', '修士', '博士', '研究科', 'daigakuin'],
    '医歯薬系':       ['医学部', '歯学部', '薬学部', '看護'],
}


def classify_doc_type_by_rule(pdf_url: str, extracted_units: dict) -> str:
    """ルールベースで doc_type を推定"""
    url_lower = (pdf_url or '').lower()
    notes = str(extracted_units.get('notes', '')).lower()
    covered_json = json.dumps(
        extracted_units.get('covered_units', []), ensure_ascii=False
    ).lower()
    search_text = url_lower + ' ' + notes + ' ' + covered_json

    for doc_type, keywords in DOC_TYPE_RULES.items():
        for kw in keywords:
            if kw.lower() in search_text:
                return doc_type

    # covered_units が存在する場合はデフォルト「募集要項」
    if extracted_units.get('covered_units'):
        return '募集要項'

    return 'その他'


def detect_exam_types(pdf_url: str, extracted_units: dict) -> list[str]:
    """入試方式タグを検出"""
    url_lower = (pdf_url or '').lower()
    notes = str(extracted_units.get('notes', ''))
    covered_json = json.dumps(
        extracted_units.get('covered_units', []), ensure_ascii=False
    )
    search_text = url_lower + ' ' + notes + ' ' + covered_json

    detected = []
    for exam_type, keywords in EXAM_TYPE_PATTERNS.items():
        for kw in keywords:
            if kw in search_text:
                detected.append(exam_type)
                break

    return list(dict.fromkeys(detected))  # 重複除去・順序保持


def classify_with_llm(records: list[dict]) -> dict[str, dict]:
    """
    LLM で doc_type を分類する（ルールベースで「その他」になったもの対象）
    Returns: {record_id: {'doc_type': ..., 'exam_types': [...]}}
    """
    if not records:
        return {}

    sys.path.insert(0, os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', 'agentic_crawler')
    ))
    from llm.client import llm_call

    results = {}
    for rec in records:
        eu = rec.get('extracted_units') or {}
        notes = eu.get('notes', '')
        covered = eu.get('covered_units', [])
        url = rec.get('pdf_url', '')

        prompt = f"""以下のPDF情報から、文書種別と入試方式を判定してください。

PDF URL: {url}
備考: {notes[:300]}
対象学部: {json.dumps(covered[:3], ensure_ascii=False)}

文書種別の選択肢:
- 募集要項（入学者募集の要項・要領）
- 選抜要項（入学者選抜の詳細）
- 出願要領（出願手続・書類）
- 入学案内（大学・学部の案内資料）
- 合格発表（合格者・成績発表）
- 学生便覧（在学生向け手引き）
- その他

入試方式（該当するものをすべて）:
一般選抜, 学校推薦型選抜, 総合型選抜, 社会人入試, 外国人留学生, 編入学, 大学院

JSON形式で回答:
{{"doc_type": "...", "exam_types": [...]}}"""

        try:
            response = llm_call(prompt, max_tokens=200)
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                results[rec['id']] = {
                    'doc_type': parsed.get('doc_type', 'その他'),
                    'exam_types': parsed.get('exam_types', [])
                }
        except Exception as e:
            print(f'LLM分類エラー [{rec["university_name"]}]: {e}', file=sys.stderr)
            results[rec['id']] = {'doc_type': 'その他', 'exam_types': []}

    return results


def main():
    parser = argparse.ArgumentParser(description='Phase 4 Step 3: 文書分類タグ付与')
    parser.add_argument('--dry-run', action='store_true', help='確認のみ（変更なし）')
    parser.add_argument('--universities', nargs='+', help='対象大学名（未指定=全大学）')
    parser.add_argument('--llm', action='store_true', help='ルール判定不可の場合LLMを使用')
    args = parser.parse_args()

    from src.db.supabase_client import get_supabase
    client = get_supabase()

    mode_str = '[DRY-RUN] ' if args.dry_run else ''
    print(f'{mode_str}Phase 4 Step 3: 文書分類タグ付与開始')
    print('=' * 65)

    # ── データ取得（除外済みを除く）──────────────────────────
    print('データ取得中...', file=sys.stderr)
    all_records = []
    page_size = 500
    offset = 0
    while True:
        q = client.table('crawled_pdfs')\
            .select('id,university_name,pdf_url,extracted_units,doc_type,is_scan_pdf')\
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

    print(f'対象レコード: {len(all_records)} 件\n')

    # ── 分類処理 ──────────────────────────────────────────────
    classified_results = []  # (id, doc_type, exam_types, is_scan_pdf)
    llm_candidates = []      # ルールで「その他」になったもの
    doc_type_counter = Counter()
    scan_pdf_count = 0

    for rec in all_records:
        eu = rec.get('extracted_units') or {}
        if isinstance(eu, str):
            try:
                eu = json.loads(eu)
            except Exception:
                eu = {}

        pdf_url = rec.get('pdf_url', '')

        # is_scan_pdf 判定
        # extracted_units の covered_units が空 + notes が空 → スキャン判定
        covered = eu.get('covered_units', [])
        notes = eu.get('notes', '')
        char_count = len(str(eu))
        is_scan = (not covered and not notes and char_count < 100)
        if is_scan:
            scan_pdf_count += 1

        # doc_type 判定
        doc_type = classify_doc_type_by_rule(pdf_url, eu)
        exam_types = detect_exam_types(pdf_url, eu)
        doc_type_counter[doc_type] += 1

        if doc_type == 'その他' and args.llm:
            llm_candidates.append(rec)
        else:
            classified_results.append((
                rec['id'], doc_type, exam_types, is_scan
            ))

    # ── LLM 補完 ─────────────────────────────────────────────
    if args.llm and llm_candidates:
        print(f'LLM 分類対象: {len(llm_candidates)} 件', file=sys.stderr)
        llm_results = classify_with_llm(llm_candidates)
        for rec in llm_candidates:
            eu = rec.get('extracted_units') or {}
            if isinstance(eu, str):
                try:
                    eu = json.loads(eu)
                except Exception:
                    eu = {}
            is_scan = (not eu.get('covered_units') and not eu.get('notes')
                       and len(str(eu)) < 100)
            llm_res = llm_results.get(rec['id'], {})
            doc_type = llm_res.get('doc_type', 'その他')
            exam_types = llm_res.get('exam_types', [])
            doc_type_counter[doc_type] += 1
            classified_results.append((
                rec['id'], doc_type, exam_types, is_scan
            ))

    # ── 結果表示 ──────────────────────────────────────────────
    print('doc_type 分類結果:')
    for dt, cnt in doc_type_counter.most_common():
        pct = cnt / len(all_records) * 100
        print(f'  {dt:<15} {cnt:>5} 件  ({pct:.1f}%)')

    print(f'\nスキャンPDF（推定）: {scan_pdf_count} 件')

    # exam_types サマリー
    all_exam_types = Counter()
    for _, _, exam_types, _ in classified_results:
        for et in exam_types:
            all_exam_types[et] += 1
    print('\n入試方式タグ分布:')
    for et, cnt in all_exam_types.most_common():
        print(f'  {et:<20} {cnt:>5} 件')

    if args.dry_run:
        print(f'\n[DRY-RUN] 実際の変更は行いません。')
        return

    # ── 実際の更新 ────────────────────────────────────────────
    print(f'\n{len(classified_results)} 件を更新中...')
    updated = 0
    for rec_id, doc_type, exam_types, is_scan in classified_results:
        client.table('crawled_pdfs').update({
            'doc_type': doc_type,
            'is_scan_pdf': is_scan,
        }).eq('id', rec_id).execute()
        updated += 1
        if updated % 100 == 0:
            print(f'  更新済み: {updated}/{len(classified_results)} 件...', file=sys.stderr)

    print(f'\n✅ 完了: {updated} 件の doc_type / is_scan_pdf を更新しました。')


if __name__ == '__main__':
    main()