# -*- coding: utf-8 -*-
"""
Phase 4A.5: full_text 全文から構造化データ抽出（Map-Reduce方式）

処理内容:
  1. crawled_pdfs.full_text（全文・截断なし）を読み込み
  2. 長文の場合はページ単位でチャンク分割（Map）
  3. 各チャンクから LLM で入試情報を抽出
  4. 複数チャンクの結果を LLM でマージ（Reduce）
  5. 結果を crawled_pdfs.structured_data（JSONB）に保存

抽出フィールド（入試方式別）:
  - 出願期間（start/end/notes）
  - 試験日
  - 合格発表日
  - 入学手続締切日
  - 募集定員
  - 試験科目・配点
  - 出願資格
  - 必要書類

使用方法:
  python scripts/phase4a5_structured_extract.py --dry-run
  python scripts/phase4a5_structured_extract.py
  python scripts/phase4a5_structured_extract.py --universities 北海道大学
  python scripts/phase4a5_structured_extract.py --limit 5  # テスト用
  python scripts/phase4a5_structured_extract.py --reprocess  # 再処理

事前条件:
  - Phase 4A 完了済み（crawled_pdfs.full_text が存在）
  - Supabase に structured_data カラムが追加済み:
    ALTER TABLE crawled_pdfs ADD COLUMN IF NOT EXISTS structured_data JSONB;
"""
import sys
import os
import json
import re
import time
import argparse
from typing import Optional

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'agentic_crawler')
))

from dotenv import load_dotenv
load_dotenv()

# ── 設定 ─────────────────────────────────────────────────────────
MAP_CHUNK_SIZE = 4000     # Map フェーズの1チャンク文字数
MAP_OVERLAP = 200         # チャンク間のオーバーラップ（文脈保持）
SLEEP_BETWEEN_PDFS = 1.0 # PDF間の待機（秒）
SLEEP_BETWEEN_LLM = 0.3  # LLM呼び出し間の待機（秒）


# ── Map フェーズ：1チャンクから情報抽出 ──────────────────────────

MAP_PROMPT = """あなたは日本の大学入試募集要項から情報を抽出する専門家です。
以下のテキストから入試に関する情報を抽出してください。

【大学名】{university_name}
【学部/研究科】{unit_name}
【テキスト】
{chunk_text}

以下のJSON形式で抽出してください。
情報が見つからない場合はそのフィールドを省略してください。
複数の入試方式がある場合はすべて抽出してください。

{{
  "exam_types": [
    {{
      "type": "入試方式名（例：一般選抜前期日程、学校推薦型選抜、総合型選抜等）",
      "target": "対象学部・学科・専攻（あれば）",
      "application_period": {{
        "start": "YYYY-MM-DD または 月日表記",
        "end": "YYYY-MM-DD または 月日表記",
        "notes": "消印有効等の注記"
      }},
      "exam_date": "試験日（YYYY-MM-DD または 月日表記）",
      "result_date": "合格発表日",
      "enrollment_deadline": "入学手続締切日",
      "capacity": 募集人員（数字）,
      "exam_subjects": [
        {{"subject": "科目名", "score": 配点数字, "notes": "備考"}}
      ],
      "qualification": "出願資格",
      "application_documents": ["必要書類1", "必要書類2"],
      "notes": "その他特記事項"
    }}
  ],
  "general_info": {{
    "academic_year": "対象年度（例：令和7年度）",
    "notes": "全体的な特記事項"
  }}
}}

JSONのみ出力してください。説明文は不要です。"""


def extract_from_chunk(
    llm_call,
    chunk_text: str,
    university_name: str,
    unit_name: str,
) -> Optional[dict]:
    """1チャンクから構造化情報を抽出する（Mapフェーズ）"""
    prompt = MAP_PROMPT.format(
        university_name=university_name,
        unit_name=unit_name or '全学',
        chunk_text=chunk_text,
    )
    try:
        response = llm_call(prompt, max_tokens=2000)
        # JSON 抽出
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            return json.loads(match.group())
    except json.JSONDecodeError as e:
        # JSON修復を試みる
        try:
            # 末尾の不完全な部分を除去
            text = match.group() if match else response
            text = re.sub(r',\s*}', '}', text)
            text = re.sub(r',\s*]', ']', text)
            return json.loads(text)
        except Exception:
            pass
    except Exception as e:
        pass
    return None


# ── Reduce フェーズ：複数チャンクの結果をマージ ───────────────────

REDUCE_PROMPT = """以下は同じPDFの異なる部分から抽出された入試情報です。
これらをマージして、重複を排除し、最も完全な情報を持つJSONを生成してください。

【大学名】{university_name}
【抽出結果リスト】
{extracted_list}

同じ入試方式の情報はマージし、情報が多い方を優先してください。
出願期間・試験日・定員など具体的な数値・日付が得られている場合は必ず保持してください。

最終的なJSONのみ出力してください。
形式は入力と同じ構造を維持してください。"""


def merge_extractions(
    llm_call,
    extractions: list[dict],
    university_name: str,
) -> dict:
    """複数チャンクの抽出結果をマージする（Reduceフェーズ）"""
    if len(extractions) == 1:
        return extractions[0]

    # 全抽出結果を結合してLLMでマージ
    extracted_list = json.dumps(extractions, ensure_ascii=False, indent=2)

    # トークン制限対策：抽出結果が多い場合は要約して渡す
    if len(extracted_list) > 8000:
        # exam_types のみ抽出してマージ
        all_exam_types = []
        general_info = {}
        for ext in extractions:
            if isinstance(ext, dict):
                all_exam_types.extend(ext.get('exam_types', []))
                if ext.get('general_info'):
                    general_info.update(ext['general_info'])
        simplified = {
            'exam_types': all_exam_types,
            'general_info': general_info
        }
        extracted_list = json.dumps(simplified, ensure_ascii=False, indent=2)

    prompt = REDUCE_PROMPT.format(
        university_name=university_name,
        extracted_list=extracted_list[:10000],  # 最大10000字
    )

    try:
        response = llm_call(prompt, max_tokens=3000)
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            result = json.loads(match.group())
            return result
    except Exception:
        pass

    # マージ失敗時は単純結合
    all_exam_types = []
    general_info = {}
    seen_types = set()
    for ext in extractions:
        if isinstance(ext, dict):
            for et in ext.get('exam_types', []):
                et_type = et.get('type', '')
                if et_type not in seen_types:
                    all_exam_types.append(et)
                    seen_types.add(et_type)
            if ext.get('general_info'):
                general_info.update(ext['general_info'])
    return {'exam_types': all_exam_types, 'general_info': general_info}


# ── テキストをチャンク分割 ────────────────────────────────────────

def split_text_for_map(full_text: str, chunk_size: int = MAP_CHUNK_SIZE) -> list[str]:
    """
    full_text をページ区切りを優先して Map 用チャンクに分割する。
    ページ区切り（--- Page N ---）を優先し、それでも大きい場合は段落で分割。
    """
    if len(full_text) <= chunk_size:
        return [full_text]

    # ページ区切りで分割
    pages = re.split(r'--- Page \d+ ---', full_text)
    pages = [p.strip() for p in pages if p.strip()]

    chunks = []
    current_chunk = ''

    for page in pages:
        if len(current_chunk) + len(page) <= chunk_size:
            current_chunk += ('\n\n' if current_chunk else '') + page
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(page) > chunk_size:
                # 1ページが大きすぎる場合は段落で分割
                paragraphs = re.split(r'\n{2,}', page)
                para_chunk = ''
                for para in paragraphs:
                    if len(para_chunk) + len(para) <= chunk_size:
                        para_chunk += ('\n\n' if para_chunk else '') + para
                    else:
                        if para_chunk:
                            chunks.append(para_chunk)
                        para_chunk = para
                current_chunk = para_chunk
            else:
                current_chunk = page

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# ── メイン処理 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Phase 4A.5: full_text全文からLLM構造化抽出（Map-Reduce）'
    )
    parser.add_argument('--dry-run', action='store_true', help='確認のみ（変更なし）')
    parser.add_argument('--universities', nargs='+', help='対象大学名')
    parser.add_argument('--limit', type=int, help='処理件数上限（テスト用）')
    parser.add_argument('--reprocess', action='store_true', help='処理済みも再処理')
    parser.add_argument('--doc-types', nargs='+',
                        default=['募集要項', '選抜要項', '出願要領'],
                        help='処理対象のdoc_type（デフォルト：募集要項・選抜要項・出願要領）')
    args = parser.parse_args()

    from src.db.supabase_client import get_supabase
    from llm.client import llm_call
    client = get_supabase()

    mode_str = '[DRY-RUN] ' if args.dry_run else ''
    print(f'{mode_str}Phase 4A.5: 構造化データ抽出 開始')
    print(f'  対象 doc_type: {args.doc_types}')
    print('=' * 65)

    # ── 対象レコード取得 ──────────────────────────────────────
    print('対象PDFを取得中...', file=sys.stderr)
    all_records = []
    page_size = 100
    offset = 0

    while True:
        q = client.table('crawled_pdfs')\
            .select('id,university_name,pdf_url,pdf_scope,actual_year,'
                    'academic_year,extracted_units,full_text,doc_type')\
            .eq('is_excluded', False)\
            .not_.is_('full_text', 'null')\
            .in_('doc_type', args.doc_types)\
            .range(offset, offset + page_size - 1)

        if args.universities:
            q = q.in_('university_name', args.universities)
        if not args.reprocess:
            q = q.is_('structured_data', 'null')

        r = q.execute()
        if not r.data:
            break
        all_records.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size
        print(f'  取得済み: {offset} 件...', file=sys.stderr)

    if args.limit:
        all_records = all_records[:args.limit]

    print(f'対象: {len(all_records)} 件\n')

    if args.dry_run:
        print('[DRY-RUN] 処理対象サンプル（最大5件）:')
        for rec in all_records[:5]:
            ft = rec.get('full_text', '') or ''
            chunks = split_text_for_map(ft)
            print(f'  [{rec["university_name"]}] {rec.get("doc_type","")}'
                  f' | {len(ft):,}字 → {len(chunks)} Mapチャンク')
        print(f'\n推定LLM呼び出し数: ~{sum(len(split_text_for_map(r.get("full_text","") or "")) for r in all_records[:5]) * len(all_records) // max(len(all_records[:5]),1)} 回')
        print('[DRY-RUN] 実際の変更は行いません。')
        return

    # ── 処理ループ ────────────────────────────────────────────
    success = 0
    failed = 0
    total_map_calls = 0

    for i, rec in enumerate(all_records, 1):
        pdf_id = rec['id']
        university_name = rec['university_name']
        full_text = rec.get('full_text', '') or ''

        # unit_name を extracted_units から取得
        eu = rec.get('extracted_units') or {}
        if isinstance(eu, str):
            try:
                eu = json.loads(eu)
            except Exception:
                eu = {}
        covered = eu.get('covered_units', [])
        unit_name = covered[0].get('unit_name', '') if covered else ''

        print(f'[{i}/{len(all_records)}] {university_name} | {len(full_text):,}字 | {rec.get("doc_type","")}')

        if not full_text:
            print(f'  ✗ full_text が空 → スキップ')
            failed += 1
            continue

        # ── Map フェーズ ──────────────────────────────────────
        map_chunks = split_text_for_map(full_text)
        print(f'  → {len(map_chunks)} Mapチャンクで処理')

        map_results = []
        for j, chunk in enumerate(map_chunks, 1):
            result = extract_from_chunk(llm_call, chunk, university_name, unit_name)
            if result:
                # 有意な情報が含まれているか確認
                exam_types = result.get('exam_types', [])
                if exam_types:
                    map_results.append(result)
                    print(f'    Map [{j}/{len(map_chunks)}]: {len(exam_types)} 入試方式を抽出')
                else:
                    print(f'    Map [{j}/{len(map_chunks)}]: 入試情報なし（スキップ）')
            else:
                print(f'    Map [{j}/{len(map_chunks)}]: 抽出失敗')
            total_map_calls += 1
            time.sleep(SLEEP_BETWEEN_LLM)

        if not map_results:
            print(f'  ✗ 有効な抽出結果なし')
            # 空のstructured_dataを保存（再処理不要マーク）
            client.table('crawled_pdfs').update({
                'structured_data': {'exam_types': [], 'general_info': {}, 'note': '抽出情報なし'}
            }).eq('id', pdf_id).execute()
            failed += 1
            time.sleep(SLEEP_BETWEEN_PDFS)
            continue

        # ── Reduce フェーズ ───────────────────────────────────
        if len(map_results) > 1:
            print(f'  → Reduceフェーズ: {len(map_results)} 結果をマージ')
            final_result = merge_extractions(llm_call, map_results, university_name)
        else:
            final_result = map_results[0]

        # メタデータ付与
        final_result['_meta'] = {
            'university_name': university_name,
            'unit_name': unit_name,
            'academic_year': rec.get('actual_year') or rec.get('academic_year', ''),
            'pdf_scope': rec.get('pdf_scope', ''),
            'pdf_url': rec.get('pdf_url', ''),
            'doc_type': rec.get('doc_type', ''),
            'map_chunks': len(map_chunks),
            'map_results': len(map_results),
        }

        # exam_types の件数をカウント
        n_exam_types = len(final_result.get('exam_types', []))
        print(f'  ✓ {n_exam_types} 入試方式の情報を抽出・保存')

        # DB 保存
        client.table('crawled_pdfs').update({
            'structured_data': final_result
        }).eq('id', pdf_id).execute()

        success += 1
        time.sleep(SLEEP_BETWEEN_PDFS)

    # ── 完了サマリー ──────────────────────────────────────────
    print('\n' + '=' * 65)
    print(f'✅ Phase 4A.5 完了')
    print(f'  成功:           {success} 件')
    print(f'  失敗/情報なし:  {failed} 件')
    print(f'  総LLM呼び出し:  {total_map_calls} 回（Mapフェーズのみ）')


if __name__ == '__main__':
    main()