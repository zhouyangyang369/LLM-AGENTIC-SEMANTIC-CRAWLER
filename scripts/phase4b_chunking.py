# -*- coding: utf-8 -*-
"""
Phase 4B: Contextual Chunking

処理内容:
  1. crawled_pdfs.full_text を章節単位でセクション分割
  2. 各 chunk に LLM で context 付与（Anthropic Contextual Retrieval 手法）
  3. メタデータ（大学名/学部/年度/入試方式/セクションパス）付与
  4. pdf_chunks テーブルへ保存

使用方法:
  python scripts/phase4b_chunking.py --dry-run
  python scripts/phase4b_chunking.py
  python scripts/phase4b_chunking.py --universities 北海道大学
  python scripts/phase4b_chunking.py --limit 5  # テスト用
  python scripts/phase4b_chunking.py --no-context  # LLM context なし（高速）
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
CHUNK_TARGET_SIZE = 600    # 目標 chunk サイズ（文字数）
CHUNK_MAX_SIZE = 900       # 最大 chunk サイズ
CHUNK_MIN_SIZE = 100       # この文字数以下は前の chunk に結合
DOC_SUMMARY_CHARS = 2000   # context 生成用の文書先頭文字数
SLEEP_BETWEEN_PDFS = 0.5  # PDF 間の待機（秒）

# 日本語募集要項に頻出する見出しパターン
HEADING_PATTERNS = [
    r'^第\d+章\s*.+',           # 第1章 募集要項
    r'^第\d+節\s*.+',           # 第1節 出願資格
    r'^[\(（]\d+[\)）]\s*.+',   # (1) 出願期間
    r'^\d+\.\s*.+',             # 1. 出願資格
    r'^\d+\.\d+\s*.+',          # 1.1 一般選抜
    r'^[■□●○◆◇▶►▷→]\.?\s*.+', # ■ 出願手続
    r'^【.+】',                  # 【出願期間】
    r'^〔.+〕',                  # 〔一般選抜〕
    r'^--- Page \d+ ---',        # ページ区切り（phase4aで挿入）
]
HEADING_RE = re.compile('|'.join(HEADING_PATTERNS), re.MULTILINE)


# ── セクション分割 ────────────────────────────────────────────────

def split_into_sections(text: str) -> list[dict]:
    """
    テキストを見出し境界でセクションに分割する。
    Returns: [{heading, content, page_number}, ...]
    """
    if not text:
        return []

    lines = text.split('\n')
    sections = []
    current_heading = '冒頭'
    current_lines = []
    current_page = 1

    for line in lines:
        # ページ番号の追跡
        page_match = re.match(r'^--- Page (\d+) ---', line)
        if page_match:
            current_page = int(page_match.group(1))
            continue

        # 見出し判定
        is_heading = bool(HEADING_RE.match(line.strip()))

        if is_heading and current_lines:
            # 現在のセクションを保存
            content = '\n'.join(current_lines).strip()
            if content:
                sections.append({
                    'heading': current_heading,
                    'content': content,
                    'page_number': current_page,
                })
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    # 最後のセクション
    if current_lines:
        content = '\n'.join(current_lines).strip()
        if content:
            sections.append({
                'heading': current_heading,
                'content': content,
                'page_number': current_page,
            })

    return sections


def sections_to_chunks(sections: list[dict]) -> list[dict]:
    """
    セクションリストを適切なサイズの chunk に変換する。
    小さすぎるセクションは結合、大きすぎるセクションは分割。
    Returns: [{chunk_text, section_path, page_number, chunk_index}, ...]
    """
    chunks = []
    chunk_index = 0
    buffer_text = ''
    buffer_heading = ''
    buffer_page = 1

    for section in sections:
        heading = section['heading']
        content = section['content']
        page = section['page_number']
        section_text = f'{heading}\n{content}' if heading != '冒頭' else content

        if len(buffer_text) + len(section_text) <= CHUNK_MAX_SIZE:
            # バッファに追加
            if not buffer_heading:
                buffer_heading = heading
                buffer_page = page
            buffer_text += ('\n\n' if buffer_text else '') + section_text
        else:
            # バッファが溜まっている場合は保存
            if len(buffer_text) >= CHUNK_MIN_SIZE:
                chunks.append({
                    'chunk_index': chunk_index,
                    'chunk_text': buffer_text.strip(),
                    'section_path': buffer_heading,
                    'page_number': buffer_page,
                })
                chunk_index += 1

            # 現セクションが大きすぎる場合は強制分割
            if len(section_text) > CHUNK_MAX_SIZE:
                # 段落単位で分割
                paragraphs = re.split(r'\n{2,}', section_text)
                para_buffer = ''
                for para in paragraphs:
                    if len(para_buffer) + len(para) <= CHUNK_MAX_SIZE:
                        para_buffer += ('\n\n' if para_buffer else '') + para
                    else:
                        if len(para_buffer) >= CHUNK_MIN_SIZE:
                            chunks.append({
                                'chunk_index': chunk_index,
                                'chunk_text': para_buffer.strip(),
                                'section_path': heading,
                                'page_number': page,
                            })
                            chunk_index += 1
                        para_buffer = para
                buffer_text = para_buffer
            else:
                buffer_text = section_text
            buffer_heading = heading
            buffer_page = page

    # 残りのバッファ
    if len(buffer_text) >= CHUNK_MIN_SIZE:
        chunks.append({
            'chunk_index': chunk_index,
            'chunk_text': buffer_text.strip(),
            'section_path': buffer_heading,
            'page_number': buffer_page,
        })

    return chunks


# ── LLM context 付与 ─────────────────────────────────────────────

def generate_context_for_pdf(
    university_name: str,
    unit_name: str,
    academic_year: str,
    pdf_scope: str,
    doc_summary: str,
    chunk_text: str,
) -> str:
    """
    chunk に文書全体のコンテキストを付与する（Contextual Retrieval）。
    PDF 単位で1回 summary を生成し、各 chunk の context 生成に使い回す。
    """
    from llm.client import llm_call

    scope_ja = {'undergraduate': '学部', 'graduate': '大学院・研究科',
                'combined': '学部・大学院'}.get(pdf_scope, pdf_scope)

    prompt = f"""以下は大学募集要項PDFの一部です。この部分が文書全体のどのセクションに属するかを、1〜2文の日本語で簡潔に説明してください。
検索インデックスへの追加用です。説明文のみ出力してください。

【文書情報】
大学名: {university_name}
対象: {unit_name or '全学'} {scope_ja}
年度: {academic_year}

【文書全体の冒頭（参考）】
{doc_summary[:DOC_SUMMARY_CHARS]}

【この chunk の内容】
{chunk_text[:500]}

【出力（1〜2文）】"""

    try:
        context = llm_call(prompt, max_tokens=150)
        return context.strip()
    except Exception as e:
        return f'{university_name} {unit_name} {academic_year} 募集要項'


# ── 入試方式タグ検出 ─────────────────────────────────────────────

def detect_exam_types_from_text(text: str) -> list[str]:
    """テキストから入試方式タグを検出"""
    patterns = {
        '一般選抜':       ['一般選抜', '一般入試', '前期日程', '後期日程', '中期日程'],
        '学校推薦型選抜': ['学校推薦', '推薦入試', '指定校推薦', '公募推薦'],
        '総合型選抜':     ['総合型', 'AO入試', 'AO選抜', '自己推薦'],
        '社会人入試':     ['社会人'],
        '外国人留学生':   ['外国人', '留学生'],
        '編入学':         ['編入', '転入'],
    }
    detected = []
    for exam_type, keywords in patterns.items():
        if any(kw in text for kw in keywords):
            detected.append(exam_type)
    return detected


# ── メイン処理 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Phase 4B: Contextual Chunking')
    parser.add_argument('--dry-run', action='store_true', help='確認のみ（変更なし）')
    parser.add_argument('--universities', nargs='+', help='対象大学名')
    parser.add_argument('--limit', type=int, help='処理PDF数上限（テスト用）')
    parser.add_argument('--no-context', action='store_true', help='LLM context 付与をスキップ')
    parser.add_argument('--reprocess', action='store_true', help='処理済みも再処理')
    args = parser.parse_args()

    from src.db.supabase_client import get_supabase
    client = get_supabase()

    mode_str = '[DRY-RUN] ' if args.dry_run else ''
    print(f'{mode_str}Phase 4B: Contextual Chunking 開始')
    print('=' * 65)

    # ── 対象PDFを取得 ─────────────────────────────────────────
    print('対象PDFを取得中...', file=sys.stderr)
    all_pdfs = []
    page_size = 100
    offset = 0
    while True:
        q = client.table('crawled_pdfs')\
            .select('id,university_name,pdf_url,pdf_scope,actual_year,academic_year,'
                    'extracted_units,full_text,doc_type')\
            .eq('is_excluded', False)\
            .not_.is_('full_text', 'null')\
            .range(offset, offset + page_size - 1)
        if args.universities:
            q = q.in_('university_name', args.universities)
        r = q.execute()
        if not r.data:
            break
        all_pdfs.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size

    if args.limit:
        all_pdfs = all_pdfs[:args.limit]

    print(f'対象PDF: {len(all_pdfs)} 件\n')

    if args.dry_run:
        total_chunks_est = 0
        for pdf in all_pdfs[:5]:
            ft = pdf.get('full_text', '') or ''
            sections = split_into_sections(ft)
            chunks = sections_to_chunks(sections)
            total_chunks_est += len(chunks)
            print(f'  [{pdf["university_name"]}] {len(ft):,}字 → {len(sections)}セクション → {len(chunks)} chunks')
        if len(all_pdfs) > 5:
            avg = total_chunks_est / min(5, len(all_pdfs))
            print(f'  ... 全体推定: {int(avg * len(all_pdfs))} chunks')
        print('\n[DRY-RUN] 実際の変更は行いません。')
        return

    # ── 処理ループ ────────────────────────────────────────────
    total_chunks = 0
    processed_pdfs = 0
    failed_pdfs = 0

    for i, pdf in enumerate(all_pdfs, 1):
        pdf_id = pdf['id']
        university_name = pdf['university_name']
        full_text = pdf.get('full_text', '') or ''
        pdf_scope = pdf.get('pdf_scope', 'combined')
        academic_year = pdf.get('actual_year') or pdf.get('academic_year', '')
        pdf_url = pdf.get('pdf_url', '')

        # unit_name は extracted_units から取得
        eu = pdf.get('extracted_units') or {}
        if isinstance(eu, str):
            try:
                eu = json.loads(eu)
            except Exception:
                eu = {}
        covered = eu.get('covered_units', [])
        unit_name = covered[0].get('unit_name', '') if covered else ''

        print(f'[{i}/{len(all_pdfs)}] {university_name} | {len(full_text):,}字')

        if not full_text:
            print(f'  ✗ full_text が空 → スキップ')
            failed_pdfs += 1
            continue

        # 既存 chunks 削除（reprocess の場合）
        if args.reprocess:
            client.table('pdf_chunks').delete().eq('pdf_id', pdf_id).execute()

        # セクション分割 → chunk 化
        sections = split_into_sections(full_text)
        chunks = sections_to_chunks(sections)
        print(f'  → {len(sections)} セクション → {len(chunks)} chunks')

        if not chunks:
            failed_pdfs += 1
            continue

        # LLM context 生成（PDF 単位で1回 summary を使い回す）
        doc_summary = full_text[:DOC_SUMMARY_CHARS]

        # chunk を DB に保存
        chunk_records = []
        for chunk in chunks:
            chunk_text = chunk['chunk_text']

            # context 付与
            if not args.no_context:
                context = generate_context_for_pdf(
                    university_name, unit_name, academic_year,
                    pdf_scope, doc_summary, chunk_text
                )
            else:
                context = f'{university_name} {unit_name} {academic_year} 募集要項'

            chunk_text_with_context = f'{context}\n\n{chunk_text}'
            exam_types = detect_exam_types_from_text(chunk_text)

            chunk_records.append({
                'pdf_id': pdf_id,
                'pdf_url': pdf_url,
                'university_name': university_name,
                'unit_name': unit_name or None,
                'unit_type': 'graduate' if pdf_scope == 'graduate' else 'undergraduate',
                'academic_year': academic_year,
                'pdf_scope': pdf_scope,
                'chunk_index': chunk['chunk_index'],
                'chunk_text': chunk_text,
                'chunk_context': context,
                'chunk_text_with_context': chunk_text_with_context,
                'section_path': chunk.get('section_path', ''),
                'page_number': chunk.get('page_number', 1),
                'exam_types': exam_types,
            })

        # バッチ挿入
        if chunk_records:
            batch_size = 50
            for j in range(0, len(chunk_records), batch_size):
                client.table('pdf_chunks').insert(
                    chunk_records[j:j + batch_size]
                ).execute()

        total_chunks += len(chunk_records)
        processed_pdfs += 1
        print(f'  ✓ {len(chunk_records)} chunks 保存')
        time.sleep(SLEEP_BETWEEN_PDFS)

    # ── 完了サマリー ──────────────────────────────────────────
    print('\n' + '=' * 65)
    print(f'✅ Phase 4B 完了')
    print(f'  処理PDF:     {processed_pdfs} 件')
    print(f'  失敗PDF:     {failed_pdfs} 件')
    print(f'  総 chunk 数: {total_chunks} 件')


if __name__ == '__main__':
    main()