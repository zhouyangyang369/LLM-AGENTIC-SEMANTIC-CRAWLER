# -*- coding: utf-8 -*-
"""
Phase 4A: PDF 全文構造化抽出

処理内容:
  1. crawled_pdfs から PDF URL を取得して再ダウンロード
  2. pdfplumber で extract_text + extract_tables（表格 Markdown 化）
  3. スキャンPDF（文字数<500）→ pymupdf で画像変換 → LLM ビジョン認識
  4. 全文テキストを crawled_pdfs.full_text フィールドに保存
  5. ページ数・文字数も記録

使用方法:
  python scripts/phase4a_extract_fulltext.py --dry-run
  python scripts/phase4a_extract_fulltext.py
  python scripts/phase4a_extract_fulltext.py --universities 北海道大学
  python scripts/phase4a_extract_fulltext.py --limit 10  # テスト用
  python scripts/phase4a_extract_fulltext.py --scan-only  # スキャンPDFのみ

事前準備:
  pip install pymupdf  # スキャンPDF対応に必要
  Supabaseで crawled_pdfs に full_text, page_count, char_count カラムを追加:
    ALTER TABLE crawled_pdfs
      ADD COLUMN IF NOT EXISTS full_text   TEXT,
      ADD COLUMN IF NOT EXISTS page_count  INTEGER,
      ADD COLUMN IF NOT EXISTS char_count  INTEGER;
"""
import sys
import os
import io
import json
import time
import argparse
import re
import hashlib
from typing import Optional

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'agentic_crawler')
))

from dotenv import load_dotenv
load_dotenv()

import pdfplumber
import httpx

# 設定
REQUEST_TIMEOUT = 30
MAX_PAGES_PER_PDF = 50       # 1PDFあたり最大処理ページ数
SCAN_PDF_THRESHOLD = 500     # この文字数未満はスキャンPDFと判定
TABLE_MAX_ROWS = 50          # 表格の最大行数（大きすぎる表は省略）
SLEEP_BETWEEN_PDFS = 1.0    # PDF間の待機時間（秒）


# ── テキスト抽出（pdfplumber）────────────────────────────────────

def extract_page_text(page) -> str:
    """1ページのテキスト + 表格を抽出してMarkdown形式で返す"""
    parts = []

    # 通常テキスト
    text = page.extract_text()
    if text:
        parts.append(text.strip())

    # 表格を Markdown テーブル化
    try:
        tables = page.extract_tables()
        for table in tables:
            if not table:
                continue
            # 空行除去
            rows = [
                [str(cell).strip() if cell else '' for cell in row]
                for row in table
                if any(cell for cell in row)
            ]
            if len(rows) > TABLE_MAX_ROWS:
                rows = rows[:TABLE_MAX_ROWS] + [['... (省略)']]
            if rows:
                # ヘッダー行
                header = rows[0]
                md = '| ' + ' | '.join(header) + ' |\n'
                md += '| ' + ' | '.join(['---'] * len(header)) + ' |\n'
                for row in rows[1:]:
                    # 列数を揃える
                    while len(row) < len(header):
                        row.append('')
                    md += '| ' + ' | '.join(row[:len(header)]) + ' |\n'
                parts.append(md)
    except Exception:
        pass  # 表格抽出失敗は無視

    return '\n'.join(parts)


def extract_text_with_tables(raw_bytes: bytes) -> tuple[str, int, int]:
    """
    PDF バイト列からテキスト + 表格を抽出する。
    Returns: (full_text, page_count, char_count)
    """
    page_texts = []
    page_count = 0

    try:
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            page_count = len(pdf.pages)
            pages = pdf.pages[:MAX_PAGES_PER_PDF]
            for i, page in enumerate(pages):
                page_text = extract_page_text(page)
                if page_text:
                    page_texts.append(f'--- Page {i+1} ---\n{page_text}')
    except Exception as e:
        return '', 0, 0

    full_text = '\n\n'.join(page_texts)
    return full_text, page_count, len(full_text)


# ── スキャンPDF: LLM ビジョン認識 ────────────────────────────────

def extract_scan_pdf_with_llm(raw_bytes: bytes, university_name: str) -> tuple[str, int]:
    """
    スキャンPDFを pymupdf で画像変換し、LLM ビジョンで認識。
    Returns: (extracted_text, page_count)
    """
    try:
        import fitz  # pymupdf
        import base64
    except ImportError:
        return '[ERROR] pymupdf が未インストールです。pip install pymupdf で導入してください。', 0

    from llm.client import llm_call

    doc = fitz.open(stream=raw_bytes, filetype='pdf')
    page_count = len(doc)
    pages_to_process = min(page_count, 10)  # 最大10ページ（コスト制御）

    all_texts = []

    for page_num in range(pages_to_process):
        page = doc[page_num]
        # ページを画像に変換（150 DPI）
        mat = fitz.Matrix(150/72, 150/72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes('png')
        img_b64 = base64.b64encode(img_bytes).decode('utf-8')

        prompt = f"""この画像は{university_name}の募集要項PDFの{page_num+1}ページ目です。
画像内のすべてのテキストを正確に抽出してください。
表格がある場合はMarkdown表形式で出力してください。
抽出テキストのみ出力し、説明文は不要です。"""

        try:
            # Claude Sonnet ビジョン API 呼び出し
            # llm_call はテキストのみ対応なので、直接 OpenAI 互換 API を使用
            import openai
            from config import OPENAI_COMPAT_BASE_URL, OPENAI_COMPAT_API_KEY, OPENAI_COMPAT_PRIMARY_MODEL

            oc_client = openai.OpenAI(
                base_url=OPENAI_COMPAT_BASE_URL,
                api_key=OPENAI_COMPAT_API_KEY,
            )
            response = oc_client.chat.completions.create(
                model=OPENAI_COMPAT_PRIMARY_MODEL,
                messages=[{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': prompt},
                        {
                            'type': 'image_url',
                            'image_url': {
                                'url': f'data:image/png;base64,{img_b64}'
                            }
                        }
                    ]
                }],
                max_tokens=2000,
            )
            page_text = response.choices[0].message.content or ''
            all_texts.append(f'--- Page {page_num+1} ---\n{page_text}')
            time.sleep(0.5)  # API レート制限回避
        except Exception as e:
            all_texts.append(f'--- Page {page_num+1} ---\n[LLM認識エラー: {e}]')

    doc.close()
    return '\n\n'.join(all_texts), page_count


# ── PDF ダウンロード ──────────────────────────────────────────────

def download_pdf(url: str) -> Optional[bytes]:
    """PDF を HTTP でダウンロードして返す"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/pdf,*/*',
    }
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.content
            else:
                return None
    except Exception as e:
        return None


# ── メイン処理 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Phase 4A: PDF 全文構造化抽出')
    parser.add_argument('--dry-run', action='store_true', help='確認のみ（変更なし）')
    parser.add_argument('--universities', nargs='+', help='対象大学名')
    parser.add_argument('--limit', type=int, help='処理件数上限（テスト用）')
    parser.add_argument('--scan-only', action='store_true', help='スキャンPDFのみ処理')
    parser.add_argument('--reprocess', action='store_true', help='処理済みも再処理')
    args = parser.parse_args()

    from src.db.supabase_client import get_supabase
    client = get_supabase()

    mode_str = '[DRY-RUN] ' if args.dry_run else ''
    print(f'{mode_str}Phase 4A: PDF 全文構造化抽出 開始')
    print('=' * 65)

    # ── 対象レコード取得 ──────────────────────────────────────
    print('対象PDFを取得中...', file=sys.stderr)
    all_records = []
    page_size = 200
    offset = 0
    while True:
        q = client.table('crawled_pdfs')\
            .select('id,university_name,pdf_url,is_scan_pdf,full_text,is_excluded')\
            .eq('is_excluded', False)\
            .range(offset, offset + page_size - 1)
        if args.universities:
            q = q.in_('university_name', args.universities)
        if args.scan_only:
            q = q.eq('is_scan_pdf', True)
        if not args.reprocess:
            q = q.is_('full_text', 'null')  # 未処理のみ
        r = q.execute()
        if not r.data:
            break
        all_records.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size

    if args.limit:
        all_records = all_records[:args.limit]

    print(f'対象: {len(all_records)} 件\n')

    if args.dry_run:
        print('[DRY-RUN] 処理対象サンプル（最大10件）:')
        for rec in all_records[:10]:
            scan_flag = ' [SCAN]' if rec.get('is_scan_pdf') else ''
            print(f'  [{rec["university_name"]}]{scan_flag} {rec.get("pdf_url","")[:70]}')
        print('\n[DRY-RUN] 実際の変更は行いません。')
        return

    # ── 処理ループ ────────────────────────────────────────────
    success = 0
    failed = 0
    scan_processed = 0

    for i, rec in enumerate(all_records, 1):
        pdf_url = rec.get('pdf_url', '')
        university_name = rec.get('university_name', '')
        is_scan = rec.get('is_scan_pdf', False)

        print(f'[{i}/{len(all_records)}] {university_name} | {pdf_url[:60]}')

        # ダウンロード
        raw_bytes = download_pdf(pdf_url)
        if not raw_bytes:
            print(f'  ✗ ダウンロード失敗')
            failed += 1
            time.sleep(SLEEP_BETWEEN_PDFS)
            continue

        # テキスト抽出
        if is_scan:
            print(f'  → スキャンPDF: LLM ビジョン認識を使用')
            full_text, page_count = extract_scan_pdf_with_llm(raw_bytes, university_name)
            char_count = len(full_text)
            scan_processed += 1
        else:
            full_text, page_count, char_count = extract_text_with_tables(raw_bytes)

            # テキストが少なすぎる場合はスキャンPDFとして再判定
            if char_count < SCAN_PDF_THRESHOLD and not is_scan:
                print(f'  → テキスト少（{char_count}字）: スキャンPDFに再判定')
                client.table('crawled_pdfs').update(
                    {'is_scan_pdf': True}
                ).eq('id', rec['id']).execute()
                full_text, page_count = extract_scan_pdf_with_llm(raw_bytes, university_name)
                char_count = len(full_text)
                scan_processed += 1

        if not full_text:
            print(f'  ✗ テキスト抽出失敗（{page_count}ページ）')
            failed += 1
            time.sleep(SLEEP_BETWEEN_PDFS)
            continue

        print(f'  ✓ {page_count}ページ / {char_count:,}字 抽出')

        # DB 保存
        client.table('crawled_pdfs').update({
            'full_text': full_text,
            'page_count': page_count,
            'char_count': char_count,
        }).eq('id', rec['id']).execute()

        success += 1
        time.sleep(SLEEP_BETWEEN_PDFS)

    # ── 完了サマリー ──────────────────────────────────────────
    print('\n' + '=' * 65)
    print(f'✅ Phase 4A 完了')
    print(f'  成功: {success} 件')
    print(f'  失敗: {failed} 件')
    print(f'  スキャンPDF LLM処理: {scan_processed} 件')


if __name__ == '__main__':
    main()