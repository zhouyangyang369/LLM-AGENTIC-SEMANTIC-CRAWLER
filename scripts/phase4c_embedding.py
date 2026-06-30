# -*- coding: utf-8 -*-
"""
Phase 4C: Embedding + Qdrant Upload

処理内容:
  1. pdf_chunks テーブルから chunk_text_with_context を取得
  2. Cohere embed-multilingual-v3（Vortex経由）でEmbedding
  3. Qdrant Cloud の pdf_chunks コレクションに保存
  4. Supabase の pdf_chunks に qdrant_id を記録

使用方法:
  python scripts/phase4c_embedding.py --dry-run
  python scripts/phase4c_embedding.py
  python scripts/phase4c_embedding.py --universities 北海道大学
  python scripts/phase4c_embedding.py --limit 50
  python scripts/phase4c_embedding.py --reprocess
"""
import sys
import os
import json
import time
import uuid
import argparse
import warnings
from typing import Optional

if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv()

# ============================================================
# 設定
# ============================================================
QDRANT_URL        = os.getenv('QDRANT_URL')
QDRANT_API_KEY    = os.getenv('QDRANT_API_KEY')
QDRANT_COLLECTION = os.getenv('QDRANT_COLLECTION', 'pdf_chunks')
COHERE_BASE_URL   = os.getenv('COHERE_BASE_URL')
COHERE_API_KEY    = os.getenv('COHERE_API_KEY')
COHERE_MODEL      = os.getenv('COHERE_MODEL', '@bedrock-uswest2/cohere.embed-multilingual-v3')

EMBED_DIM    = 1024   # cohere embed-multilingual-v3 の次元数
BATCH_SIZE   = 50     # 1回のEmbedding APIリクエストのchunk数
SLEEP_BATCH  = 1.0    # バッチ間の待機（秒）

# 実験対象10国立大学
EXP_UNIVERSITIES = [
    '山形大学', '大阪大学', '福島大学', '横浜国立大学',
    '名古屋工業大学', '上越教育大学', '旭川医科大学',
    '北見工業大学', '東京外国語大学', '金沢大学',
]


# ============================================================
# Cohere Embedding（Vortex経由）
# ============================================================
def embed_texts(texts: list) -> Optional[list]:
    """
    Vortex経由でCohere embed-multilingual-v3を呼び出す。
    Returns: [[float, ...], ...] shape=(len(texts), 1024)
    """
    import requests

    url = COHERE_BASE_URL.rstrip('/') + '/embeddings'
    headers = {
        'Authorization': 'Bearer ' + COHERE_API_KEY,
        'Content-Type': 'application/json',
    }
    payload = {
        'model': COHERE_MODEL,
        'input': texts,
        'input_type': 'search_document',
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        # OpenAI 互換形式（Vortex経由）
        # { "data": [{"object": "embedding", "embedding": [...]}] }
        if 'data' in data:
            return [item['embedding'] for item in data['data']]

        # Cohere ネイティブ形式（フォールバック）
        if 'embeddings' in data:
            emb = data['embeddings']
            if isinstance(emb, dict) and 'float' in emb:
                return emb['float']
            if isinstance(emb, list):
                return emb

        print('    [ERROR] 予期しないレスポンス形式: {}'.format(list(data.keys())))
        return None

    except Exception as e:
        print('    [ERROR] Embedding API エラー: {}'.format(e))
        return None


# ============================================================
# Qdrant ヘルパー（requests ベース・SSL verify 無効）
# ============================================================
import requests as _requests

QDRANT_HEADERS = None  # main() で初期化

def _qdrant_headers():
    return {'api-key': QDRANT_API_KEY, 'Content-Type': 'application/json'}

def init_qdrant_collection():
    """コレクションが存在しなければ作成する。"""
    resp = _requests.get(
        QDRANT_URL.rstrip('/') + '/collections/' + QDRANT_COLLECTION,
        headers=_qdrant_headers(), verify=False, timeout=15
    )
    if resp.status_code == 200:
        count = resp.json()['result']['points_count']
        print('既存コレクション "{}" ({} points)'.format(QDRANT_COLLECTION, count))
    else:
        print('Qdrant コレクション "{}" を作成中...'.format(QDRANT_COLLECTION))
        _requests.put(
            QDRANT_URL.rstrip('/') + '/collections/' + QDRANT_COLLECTION,
            headers=_qdrant_headers(),
            json={'vectors': {'size': EMBED_DIM, 'distance': 'Cosine'}},
            verify=False, timeout=15
        )
        print('コレクション作成完了')

def qdrant_upsert(points_data: list):
    """Qdrant に points を upsert する（requests ベース）。"""
    resp = _requests.put(
        QDRANT_URL.rstrip('/') + '/collections/' + QDRANT_COLLECTION + '/points',
        headers=_qdrant_headers(),
        json={'points': points_data},
        verify=False, timeout=60
    )
    resp.raise_for_status()
    return resp.json()

def qdrant_scroll_ids():
    """Qdrant の全 point ID を取得する。"""
    existing_ids = set()
    next_offset = None
    while True:
        payload = {'limit': 1000, 'with_payload': False, 'with_vector': False}
        if next_offset:
            payload['offset'] = next_offset
        resp = _requests.post(
            QDRANT_URL.rstrip('/') + '/collections/' + QDRANT_COLLECTION + '/points/scroll',
            headers=_qdrant_headers(), json=payload, verify=False, timeout=30
        )
        data = resp.json().get('result', {})
        for p in data.get('points', []):
            existing_ids.add(str(p['id']))
        next_offset = data.get('next_page_offset')
        if not next_offset:
            break
    return existing_ids


# ============================================================
# メイン
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='Phase 4C: Embedding + Qdrant Upload')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--universities', nargs='+')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--reprocess', action='store_true')
    args = parser.parse_args()

    import requests as req
    import urllib3
    urllib3.disable_warnings()

    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    sb_headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': 'Bearer ' + SUPABASE_KEY,
        'Content-Type': 'application/json',
    }

    if args.all:
        target_universities = None
    elif args.universities:
        target_universities = args.universities
    else:
        target_universities = EXP_UNIVERSITIES

    mode_str = '[DRY-RUN] ' if args.dry_run else ''
    print('{}Phase 4C: Embedding + Qdrant Upload 開始'.format(mode_str))
    print('  対象大学: {}'.format(target_universities or '全大学'))
    print('  Qdrant  : {}'.format(QDRANT_URL))
    print('  Collection: {}'.format(QDRANT_COLLECTION))
    print('  Model   : {}'.format(COHERE_MODEL))
    print('=' * 70)

        # Qdrant コレクション初期化
    if not args.dry_run:
        init_qdrant_collection()

        # pdf_chunks レコード取得（Supabase REST API 直接呼び出し）
    all_chunks = []
    offset = 0
    page_size = 1000
    while True:
        params = {
            'select': 'id,pdf_id,university_name,unit_name,academic_year,pdf_scope,chunk_index,chunk_text,chunk_context,chunk_text_with_context,section_path,page_number,exam_types,pdf_url',
            'offset': offset,
            'limit': page_size,
        }
        if target_universities:
            params['university_name'] = 'in.(' + ','.join(target_universities) + ')'
        r = req.get(
            SUPABASE_URL.rstrip('/') + '/rest/v1/pdf_chunks',
            headers=sb_headers, params=params, verify=False, timeout=30
        )
        data = r.json()
        if not data:
            break
        all_chunks.extend(data)
        if len(data) < page_size:
            break
        offset += page_size

    print('取得 chunk 数: {:,} 件'.format(len(all_chunks)))

        # reprocess しない場合、既に Qdrant に登録済みの chunk を除外
    if not args.reprocess:
        existing_ids = qdrant_scroll_ids()
        print('Qdrant 既存 points: {:,} 件'.format(len(existing_ids)))
        all_chunks = [c for c in all_chunks if str(c['id']) not in existing_ids]
        print('未処理 chunk 数: {:,} 件'.format(len(all_chunks)))

    if args.limit:
        all_chunks = all_chunks[:args.limit]

    print('処理対象: {:,} 件\n'.format(len(all_chunks)))

    # dry-run
    if args.dry_run:
        total_batches = (len(all_chunks) + BATCH_SIZE - 1) // BATCH_SIZE
        print('推定バッチ数: {} 回'.format(total_batches))
        print('推定所要時間: 約 {} 秒'.format(int(total_batches * (SLEEP_BATCH + 2))))
        by_univ = {}
        for c in all_chunks:
            u = c['university_name']
            by_univ[u] = by_univ.get(u, 0) + 1
        print('\n大学別 chunk 数:')
        for u, cnt in sorted(by_univ.items(), key=lambda x: -x[1]):
            print('  {:<20} {:>5} chunks'.format(u, cnt))
        print('\n[DRY-RUN] 実際の変更は行いません。')
        return

    # Embedding + Qdrant Upload ループ
    success = 0
    failed = 0
    total_batches = (len(all_chunks) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_i in range(total_batches):
        batch = all_chunks[batch_i * BATCH_SIZE:(batch_i + 1) * BATCH_SIZE]
        texts = [c.get('chunk_text_with_context') or c.get('chunk_text') or '' for c in batch]

        print('[{}/{}] {} chunks embed中...'.format(
            batch_i + 1, total_batches, len(batch)))

        # Embedding
        vectors = embed_texts(texts)
        if vectors is None or len(vectors) != len(batch):
            print('  FAILED: embedding エラー（{}件スキップ）'.format(len(batch)))
            failed += len(batch)
            time.sleep(SLEEP_BATCH)
            continue

                # Qdrant Point 作成（requests ベース）
        points_data = []
        for chunk, vector in zip(batch, vectors):
            points_data.append({
                'id': str(chunk['id']),
                'vector': vector,
                'payload': {
                    'supabase_id':     chunk['id'],
                    'pdf_id':          chunk.get('pdf_id'),
                    'university_name': chunk.get('university_name', ''),
                    'unit_name':       chunk.get('unit_name', '') or '',
                    'academic_year':   chunk.get('academic_year', '') or '',
                    'pdf_scope':       chunk.get('pdf_scope', '') or '',
                    'chunk_index':     chunk.get('chunk_index', 0),
                    'section_path':    chunk.get('section_path', '') or '',
                    'page_number':     chunk.get('page_number', 1),
                    'exam_types':      chunk.get('exam_types') or [],
                    'pdf_url':         chunk.get('pdf_url', '') or '',
                    'chunk_text':      chunk.get('chunk_text', ''),
                    'chunk_context':   chunk.get('chunk_context', '') or '',
                }
            })

        # Qdrant に upsert
        try:
            qdrant_upsert(points_data)
            success += len(points_data)
            print('  saved: {} points'.format(len(points_data)))
        except Exception as e:
            print('  FAILED: Qdrant upsert エラー: {}'.format(e))
            failed += len(batch)

        time.sleep(SLEEP_BATCH)

    print('\n' + '=' * 70)
    print('Phase 4C done')
    print('  success: {:,} chunks'.format(success))
    print('  failed:  {:,} chunks'.format(failed))
    print('  Qdrant collection: {}'.format(QDRANT_COLLECTION))

        # 最終 Qdrant 件数確認
    try:
        r = _requests.get(
            QDRANT_URL.rstrip('/') + '/collections/' + QDRANT_COLLECTION,
            headers=_qdrant_headers(), verify=False, timeout=10
        )
        print('  Qdrant total points: {:,}'.format(r.json()['result']['points_count']))
    except Exception:
        pass


if __name__ == '__main__':
    main()
