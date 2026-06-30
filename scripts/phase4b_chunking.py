# -*- coding: utf-8 -*-
"""
Phase 4B: Chunking

処理内容:
  1. crawled_pdfs.full_text をページ・見出し境界でchunk分割
  2. crawled_pdfs.structured_data の情報をメタデータとして各chunkに付与
  3. chunk_context = 大学名・学部・年度・入試方式サマリー（LLM不使用）
  4. pdf_chunks テーブルへ保存

【structured_data の活用方針】
  chunk_context に以下を自動生成（LLMなし・コスト0）:
    [大学名 | 学部名 | 年度 | doc_type]
    [入試方式: 一般選抜前期・推薦型・総合型 | 定員合計: XX名]
    [出願期間: YYYY-MM-DD ~ YYYY-MM-DD]
  → embedding 時に chunk 本文と結合して文書全体コンテキストを付与

使用方法:
  python scripts/phase4b_chunking.py --dry-run
  python scripts/phase4b_chunking.py
  python scripts/phase4b_chunking.py --universities 北海道大学 東北大学
  python scripts/phase4b_chunking.py --limit 5
  python scripts/phase4b_chunking.py --reprocess
  python scripts/phase4b_chunking.py --all
"""
import sys
import os
import json
import re
import time
import argparse
from typing import Optional

if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'agentic_crawler')
))

from dotenv import load_dotenv
load_dotenv()

# ============================================================
# 設定
# ============================================================
CHUNK_TARGET_SIZE = 600   # 目標 chunk サイズ（文字数）
CHUNK_MAX_SIZE    = 900   # 最大 chunk サイズ
CHUNK_MIN_SIZE    = 80    # この文字数以下は前の chunk に結合
SLEEP_BETWEEN_PDFS = 0.3  # PDF 間の待機（秒）

# 実験対象10国立大学
EXP_UNIVERSITIES = [
    '山形大学', '大阪大学', '福島大学', '横浜国立大学',
    '名古屋工業大学', '上越教育大学', '旭川医科大学',
    '北見工業大学', '東京外国語大学', '金沢大学',
]

# 見出しパターン（日本語募集要項に頻出）
HEADING_PATTERNS = [
    r'^第\d+章\s*.+',
    r'^第\d+節\s*.+',
    r'^[\(（]\d+[\)）]\s*.+',
    r'^\d+\.\s*.+',
    r'^\d+\.\d+\s*.+',
    r'^[■□●○◆◇]\s*.+',
    r'^【.+】',
    r'^〔.+〕',
]
HEADING_RE = re.compile('|'.join(HEADING_PATTERNS), re.MULTILINE)

# ============================================================
# structured_data からコンテキスト文字列を生成
# ============================================================
def build_context_from_structured_data(
    university_name: str,
    unit_name: str,
    academic_year: str,
    pdf_scope: str,
    doc_type: str,
    structured_data: Optional[dict],
) -> str:
    """
    structured_data の情報から chunk_context 文字列を生成する。
    LLM 不使用・コスト0。
    embedding 時に chunk_text の前に結合して使う。
    """
    scope_ja = {
        'undergraduate': '学部',
        'graduate': '大学院・研究科',
        'combined': '学部・大学院',
    }.get(pdf_scope or '', '')

    lines = []

    # 基本情報
    base = ' | '.join(filter(None, [
        university_name,
        unit_name,
        academic_year,
        scope_ja,
        doc_type,
    ]))
    lines.append('[' + base + ']')

    # structured_data がない場合はここで終了
    if not structured_data or not isinstance(structured_data, dict):
        return '\n'.join(lines)

    exam_types = structured_data.get('exam_types', [])
    if not exam_types:
        return '\n'.join(lines)

    # 入試方式一覧（最大5件）
    type_names = [et.get('type', '') for et in exam_types if et.get('type')]
    if type_names:
        lines.append('[入試方式: {}]'.format(' / '.join(type_names[:5])))

    # 定員合計
    capacities = [et.get('capacity') for et in exam_types if et.get('capacity')]
    if capacities:
        try:
            total = sum(int(c) for c in capacities if str(c).isdigit())
            if total > 0:
                lines.append('[定員合計: {}名]'.format(total))
        except Exception:
            pass

    # 出願期間（最初の入試方式から取得）
    for et in exam_types:
        ap = et.get('application_period', {})
        if ap and (ap.get('start') or ap.get('end')):
            start = ap.get('start', '')
            end = ap.get('end', '')
            period = '{} ~ {}'.format(start, end) if start and end else (start or end)
            lines.append('[出願期間: {}]'.format(period))
            break

    # 試験日（最初の入試方式から取得）
    for et in exam_types:
        exam_date = et.get('exam_date', '')
        if exam_date:
            lines.append('[試験日: {}]'.format(exam_date))
            break

    return '\n'.join(lines)


# ============================================================
# 入試方式タグ検出
# ============================================================
def extract_exam_type_tags(
    structured_data: Optional[dict],
    chunk_text: str,
) -> list:
    """
    structured_data の exam_types から入試方式タグを生成。
    structured_data がない場合はテキストから検出。
    """
    if structured_data and isinstance(structured_data, dict):
        exam_types = structured_data.get('exam_types', [])
        if exam_types:
            tags = set()
            for et in exam_types:
                t = et.get('type', '')
                if '一般' in t or '前期' in t or '後期' in t:
                    tags.add('一般選抜')
                if '推薦' in t:
                    tags.add('学校推薦型選抜')
                if '総合型' in t or 'AO' in t:
                    tags.add('総合型選抜')
                if '社会人' in t:
                    tags.add('社会人入試')
                if '留学生' in t or '外国人' in t:
                    tags.add('外国人留学生')
                if '編入' in t:
                    tags.add('編入学')
            if tags:
                return sorted(tags)

    # フォールバック：テキストから検出
    patterns = {
        '一般選抜':       ['一般選抜', '一般入試', '前期日程', '後期日程'],
        '学校推薦型選抜': ['学校推薦', '推薦入試', '指定校推薦'],
        '総合型選抜':     ['総合型', 'AO入試', 'AO選抜'],
        '社会人入試':     ['社会人'],
        '外国人留学生':   ['外国人', '留学生'],
        '編入学':         ['編入', '転入'],
    }
    detected = []
    for exam_type, keywords in patterns.items():
        if any(kw in chunk_text for kw in keywords):
            detected.append(exam_type)
    return detected


# ============================================================
# テキスト分割
# ============================================================
def split_into_sections(text: str) -> list:
    """テキストを見出し境界でセクションに分割する。"""
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

        is_heading = bool(HEADING_RE.match(line.strip())) and len(line.strip()) > 2

        if is_heading and current_lines:
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

    if current_lines:
        content = '\n'.join(current_lines).strip()
        if content:
            sections.append({
                'heading': current_heading,
                'content': content,
                'page_number': current_page,
            })

    return sections




def force_split_long_chunk(text: str, max_size: int = 900) -> list:
    """
    max_size を超えるテキストを句末（。！？
）で強制分割する。
    句末が見つからない場合は max_size 文字で強制カット。
    """
    if len(text) <= max_size:
        return [text]

    parts = []
    remaining = text
    while len(remaining) > max_size:
        # max_size 以内で最後の句末を探す
        window = remaining[:max_size]
        # 句末候補（後ろから探す）
        cut_pos = -1
        for i in range(len(window) - 1, max_size // 2, -1):
            if window[i] in ('。', '！', '？', '\n'):
                cut_pos = i + 1
                break
        if cut_pos == -1:
            # 句末が見つからない場合は強制カット
            cut_pos = max_size
        parts.append(remaining[:cut_pos].strip())
        remaining = remaining[cut_pos:].strip()
    if remaining:
        parts.append(remaining)
    return [p for p in parts if p]

def sections_to_chunks(sections: list) -> list:
    """セクションリストを適切なサイズの chunk に変換する。"""
    chunks = []
    chunk_index = 0
    buffer_text = ''
    buffer_heading = ''
    buffer_page = 1

    for section in sections:
        heading = section['heading']
        content = section['content']
        page = section['page_number']
        section_text = (heading + '\n' + content) if heading != '冒頭' else content

        if len(buffer_text) + len(section_text) <= CHUNK_MAX_SIZE:
            if not buffer_heading:
                buffer_heading = heading
                buffer_page = page
            buffer_text += ('\n\n' if buffer_text else '') + section_text
        else:
            if len(buffer_text) >= CHUNK_MIN_SIZE:
                for sub in force_split_long_chunk(buffer_text.strip()):
                    if len(sub) >= CHUNK_MIN_SIZE:
                        chunks.append({
                            'chunk_index': chunk_index,
                            'chunk_text': sub,
                            'section_path': buffer_heading,
                            'page_number': buffer_page,
                        })
                        chunk_index += 1

            if len(section_text) > CHUNK_MAX_SIZE:
                # 段落単位で強制分割
                paragraphs = re.split(r'\n{2,}', section_text)
                para_buffer = ''
                for para in paragraphs:
                    if len(para_buffer) + len(para) <= CHUNK_MAX_SIZE:
                        para_buffer += ('\n\n' if para_buffer else '') + para
                    else:
                        if len(para_buffer) >= CHUNK_MIN_SIZE:
                            for sub in force_split_long_chunk(para_buffer.strip()):
                                if len(sub) >= CHUNK_MIN_SIZE:
                                    chunks.append({
                                        'chunk_index': chunk_index,
                                        'chunk_text': sub,
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

    if len(buffer_text) >= CHUNK_MIN_SIZE:
        for sub in force_split_long_chunk(buffer_text.strip()):
            if len(sub) >= CHUNK_MIN_SIZE:
                chunks.append({
                    'chunk_index': chunk_index,
                    'chunk_text': sub,
                    'section_path': buffer_heading,
                    'page_number': buffer_page,
                })
                chunk_index += 1

    return chunks


# ============================================================
# メイン
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='Phase 4B: Chunking')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--universities', nargs='+', help='対象大学名（省略時は実験用10大学）')
    parser.add_argument('--all', action='store_true', help='full_textありの全大学を対象')
    parser.add_argument('--limit', type=int, help='処理PDF数上限')
    parser.add_argument('--reprocess', action='store_true', help='処理済みも再処理')
    parser.add_argument('--doc-types', nargs='+',
                        default=['募集要項', '選抜要項', '出願要領'],
                        help='処理対象のdoc_type')
    args = parser.parse_args()

    from src.db.supabase_client import get_supabase
    client = get_supabase()

    if args.all:
        target_universities = None
    elif args.universities:
        target_universities = args.universities
    else:
        target_universities = EXP_UNIVERSITIES

    mode_str = '[DRY-RUN] ' if args.dry_run else ''
    print('{}Phase 4B: Chunking 開始'.format(mode_str))
    print('  対象大学: {}'.format(target_universities or '全大学'))
    print('  対象 doc_type: {}'.format(args.doc_types))
    print('=' * 70)

    # PDFレコード取得
    all_pdfs = []
    page_size = 100
    offset = 0
    while True:
        q = (
            client.table('crawled_pdfs')
            .select('id,university_name,pdf_url,pdf_scope,actual_year,academic_year,'
                    'extracted_units,full_text,doc_type,structured_data')
            .eq('is_excluded', False)
            .not_.is_('full_text', 'null')
            .in_('doc_type', args.doc_types)
            .range(offset, offset + page_size - 1)
        )
        if target_universities:
            q = q.in_('university_name', target_universities)
        r = q.execute()
        if not r.data:
            break
        all_pdfs.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size

    if args.limit:
        all_pdfs = all_pdfs[:args.limit]

    # reprocess しない場合、既に chunks がある PDF を除外
    if not args.reprocess:
        pdf_ids = [p['id'] for p in all_pdfs]
        chunked_ids = set()
        for i in range(0, len(pdf_ids), 100):
            batch = pdf_ids[i:i+100]
            r2 = client.table('pdf_chunks').select('pdf_id').in_('pdf_id', batch).execute()
            for row in (r2.data or []):
                chunked_ids.add(row['pdf_id'])
        all_pdfs = [p for p in all_pdfs if p['id'] not in chunked_ids]
        print('既処理を除外後: {} 件\n'.format(len(all_pdfs)))
    else:
        print('対象PDF: {} 件\n'.format(len(all_pdfs)))

    # dry-run
    if args.dry_run:
        total_chunks_est = 0
        print('{:<20} {:<8} {:>10}  {:>8}  {:>8}'.format(
            '大学名', 'doc_type', '文字数', 'sections', 'chunks'))
        print('-' * 65)
        for pdf in all_pdfs:
            ft = pdf.get('full_text', '') or ''
            sections = split_into_sections(ft)
            chunks = sections_to_chunks(sections)
            total_chunks_est += len(chunks)
            print('{:<20} {:<8} {:>10,}字  {:>8}  {:>8}'.format(
                pdf['university_name'][:20],
                pdf.get('doc_type', '')[:8],
                len(ft),
                len(sections),
                len(chunks),
            ))
        print('\n推定 chunk 総数: {}'.format(total_chunks_est))
        print('[DRY-RUN] 実際の変更は行いません。')
        return

    # 処理ループ
    total_chunks = 0
    success_pdfs = 0
    failed_pdfs = 0

    for i, pdf in enumerate(all_pdfs, 1):
        pdf_id = pdf['id']
        university_name = pdf['university_name']
        full_text = pdf.get('full_text', '') or ''
        pdf_scope = pdf.get('pdf_scope') or 'combined'
        academic_year = pdf.get('actual_year') or pdf.get('academic_year') or ''
        pdf_url = pdf.get('pdf_url', '')
        doc_type = pdf.get('doc_type', '')

        # structured_data 取得
        sd = pdf.get('structured_data')
        if isinstance(sd, str):
            try:
                sd = json.loads(sd)
            except Exception:
                sd = None

        # unit_name 取得
        eu = pdf.get('extracted_units') or {}
        if isinstance(eu, str):
            try:
                eu = json.loads(eu)
            except Exception:
                eu = {}
        covered = eu.get('covered_units', [])
        unit_name = covered[0].get('unit_name', '') if covered else ''

        # structured_data から unit_name を補完
        if not unit_name and sd:
            meta = sd.get('_meta', {})
            unit_name = meta.get('unit_name', '')

        print('[{}/{}] {} | {:,}字 | {} | {}'.format(
            i, len(all_pdfs), university_name, len(full_text),
            doc_type, academic_year,
        ))

        if not full_text or len(full_text) < 80:
            print('  skipped: full_text too short')
            failed_pdfs += 1
            continue

        # 再処理の場合は既存 chunks 削除
        if args.reprocess:
            client.table('pdf_chunks').delete().eq('pdf_id', pdf_id).execute()

        # chunk_context 生成（LLM不使用）
        chunk_context = build_context_from_structured_data(
            university_name, unit_name, academic_year,
            pdf_scope, doc_type, sd,
        )

        # exam_type タグ
        exam_type_tags = extract_exam_type_tags(sd, full_text[:2000])

        # セクション分割 → chunk 化
        sections = split_into_sections(full_text)
        chunks = sections_to_chunks(sections)
        print('  sections: {} | chunks: {} | context: {}字'.format(
            len(sections), len(chunks), len(chunk_context)))

        if not chunks:
            print('  skipped: no chunks generated')
            failed_pdfs += 1
            continue

        # DB 保存
        chunk_records = []
        for chunk in chunks:
            chunk_text = chunk['chunk_text']
            chunk_text_with_context = chunk_context + '\n\n' + chunk_text

            # chunk 固有の入試方式タグ（テキストから追加検出）
            chunk_exam_types = list(set(
                exam_type_tags + extract_exam_type_tags(None, chunk_text)
            ))

            chunk_records.append({
                'pdf_id': pdf_id,
                'pdf_url': pdf_url,
                'university_name': university_name,
                'unit_name': unit_name or None,
                'academic_year': academic_year,
                'pdf_scope': pdf_scope,
                'chunk_index': chunk['chunk_index'],
                'chunk_text': chunk_text,
                'chunk_context': chunk_context,
                'chunk_text_with_context': chunk_text_with_context,
                'section_path': chunk.get('section_path', ''),
                'page_number': chunk.get('page_number', 1),
                'exam_types': chunk_exam_types,
            })

        # バッチ挿入（50件ずつ）
        batch_size = 50
        for j in range(0, len(chunk_records), batch_size):
            client.table('pdf_chunks').insert(
                chunk_records[j:j + batch_size]
            ).execute()

        total_chunks += len(chunk_records)
        success_pdfs += 1
        print('  saved: {} chunks'.format(len(chunk_records)))
        time.sleep(SLEEP_BETWEEN_PDFS)

    print('\n' + '=' * 70)
    print('Phase 4B done')
    print('  success PDFs: {}'.format(success_pdfs))
    print('  failed PDFs:  {}'.format(failed_pdfs))
    print('  total chunks: {}'.format(total_chunks))


if __name__ == '__main__':
    main()
