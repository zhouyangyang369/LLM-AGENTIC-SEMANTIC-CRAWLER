# -*- coding: utf-8 -*-
"""
Phase 4C: Embedding 生成 + Qdrant Cloud 保存

処理内容:
  1. pdf_chunks テーブルから chunk を取得
  2. Embedding モデルでベクトル生成
  3. Qdrant Cloud のコレクションに保存
     - vector: 1024次元
     - payload: chunk_id / university_name / unit_name /
                academic_year / pdf_scope / exam_types
               （Qdrant 側フィルタ用）
  4. Supabase pdf_chunks には embedding を保存しない
     → Qdrant の point_id = pdf_chunks.id（UUID）で紐付け

設定方法（.env または agentic_crawler/config.py）:
  # Embedding モデル（JV Vortex 経由）
  OPENAI_COMPAT_BASE_URL   = https://ai-jv.vortex.sandisk.com/v1/
  OPENAI_COMPAT_API_KEY    = <your_key>
  EMBED_MODEL              = cohere.embed-multilingual-v3  # デフォルト

  # Qdrant Cloud
  QDRANT_URL               = https://xxxxxxxx.qdrant.io
  QDRANT_API_KEY           = <your_qdrant_api_key>
  QDRANT_COLLECTION        = pdf_chunks  # デフォルト

使用方法:
  python scripts/phase4c_embedding.py --dry-run
  python scripts/phase4c_embedding.py
  python scripts/phase4c_embedding.py --universities 北海道大学
  python scripts/phase4c_embedding.py --batch-size 96
  python scripts/phase4c_embedding.py --create-collection  # 初回のみ
"""
import sys
import os
import time
import argparse
import uuid
from typing import Optional

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv()

# ── 設定（.env で上書き可） ────────────────────────────────────────
# Embedding
EMBED_MODEL      = os.environ.get('EMBED_MODEL', 'cohere.embed-multilingual-v3')
EMBED_DIMENSION  = int(os.environ.get('EMBED_DIMENSION', '1024'))
INPUT_TYPE       = 'search_document'   # Cohere 用

# Qdrant
QDRANT_URL        = os.environ.get('QDRANT_URL', '')          # 必須
QDRANT_API_KEY    = os.environ.get('QDRANT_API_KEY', '')      # 必須
QDRANT_COLLECTION = os.environ.get('QDRANT_COLLECTION', 'pdf_chunks')

# 処理
DEFAULT_BATCH_SIZE    = 96
SLEEP_BETWEEN_BATCHES = 0.5


# ── Embedding クライアント ────────────────────────────────────────

def get_embed_client():
    """JV Vortex OpenAI 互換 API 経由の Embedding クライアント"""
    import openai
    base_url = os.environ.get(
        'OPENAI_COMPAT_BASE_URL',
        'https://ai-jv.vortex.sandisk.com/v1/'
    )
    api_key = os.environ.get('OPENAI_COMPAT_API_KEY', '')
    return openai.OpenAI(base_url=base_url, api_key=api_key)


def embed_texts(client, texts: list[str]) -> Optional[list[list[float]]]:
    """テキストリストをベクトル化する"""
    try:
        response = client.embeddings.create(
            model=EMBED_MODEL,
            input=texts,
            extra_body={'input_type': INPUT_TYPE},
        )
        return [item.embedding for item in response.data]
    except Exception:
        # input_type なしで再試行
        try:
            response = client.embeddings.create(
                model=EMBED_MODEL,
                input=texts,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            print(f'  Embedding エラー: {e}', file=sys.stderr)
            return None


# ── Qdrant クライアント ───────────────────────────────────────────

def get_qdrant_client():
    """Qdrant Cloud クライアントを返す"""
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        print('ERROR: qdrant-client が未インストールです。')
        print('  pip install qdrant-client でインストールしてください。')
        sys.exit(1)

    if not QDRANT_URL or not QDRANT_API_KEY:
        print('ERROR: QDRANT_URL と QDRANT_API_KEY を .env に設定してください。')
        print('  QDRANT_URL=https://xxxxxxxx.qdrant.io')
        print('  QDRANT_API_KEY=<your_api_key>')
        sys.exit(1)

    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def create_collection(qdrant_client):
    """Qdrant にコレクションを作成する（初回のみ）"""
    from qdrant_client.models import Distance, VectorParams

    try:
        existing = qdrant_client.get_collections()
        existing_names = [c.name for c in existing.collections]
        if QDRANT_COLLECTION in existing_names:
            print(f'コレクション "{QDRANT_COLLECTION}" は既に存在します。')
            return

        qdrant_client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(
                size=EMBED_DIMENSION,
                distance=Distance.COSINE,
            ),
        )
        print(f'✅ コレクション "{QDRANT_COLLECTION}" を作成しました。')
        print(f'   次元数: {EMBED_DIMENSION}, 距離: COSINE')
    except Exception as e:
        print(f'コレクション作成エラー: {e}')
        sys.exit(1)


def upsert_to_qdrant(
    qdrant_client,
    chunk_ids: list[str],
    embeddings: list[list[float]],
    payloads: list[dict],
):
    """Qdrant にベクトルと payload を upsert する"""
    from qdrant_client.models import PointStruct

    points = [
        PointStruct(
            id=str(chunk_id),   # UUID 文字列をそのまま使用
            vector=embedding,
            payload=payload,
        )
        for chunk_id, embedding, payload in zip(chunk_ids, embeddings, payloads)
    ]

    qdrant_client.upsert(
        collection_name=QDRANT_COLLECTION,
        points=points,
    )


# ── メイン処理 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Phase 4C: Embedding → Qdrant 保存')
    parser.add_argument('--dry-run', action='store_true', help='確認のみ（変更なし）')
    parser.add_argument('--universities', nargs='+', help='対象大学名')
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE,
                        help=f'バッチサイズ（デフォルト: {DEFAULT_BATCH_SIZE}）')
    parser.add_argument('--create-collection', action='store_true',
                        help='Qdrant コレクションを作成（初回のみ）')
    parser.add_argument('--reprocess', action='store_true',
                        help='Qdrant に既存データがあっても再処理（upsert）')
    parser.add_argument('--limit', type=int, help='処理 chunk 数上限（テスト用）')
    args = parser.parse_args()

    from src.db.supabase_client import get_supabase
    supabase = get_supabase()

    mode_str = '[DRY-RUN] ' if args.dry_run else ''
    print(f'{mode_str}Phase 4C: Embedding 生成 + Qdrant 保存 開始')
    print(f'  Embedding モデル : {EMBED_MODEL}')
    print(f'  次元数           : {EMBED_DIMENSION}')
    print(f'  Qdrant URL       : {QDRANT_URL or "(未設定)"}')
    print(f'  Qdrant Collection: {QDRANT_COLLECTION}')
    print('=' * 65)

    # ── Qdrant 接続・コレクション作成 ────────────────────────
    if not args.dry_run:
        qdrant = get_qdrant_client()
        if args.create_collection:
            create_collection(qdrant)
    else:
        qdrant = None

    # ── 対象 chunk 取得（Supabase）───────────────────────────
    print('対象 chunk を取得中...', file=sys.stderr)
    all_chunks = []
    page_size = 1000
    offset = 0

    while True:
        q = supabase.table('pdf_chunks')\
            .select(
                'id,university_name,unit_name,unit_type,'
                'academic_year,pdf_scope,exam_types,'
                'chunk_text_with_context,chunk_text,'
                'section_path,page_number,pdf_url,pdf_id'
            )\
            .range(offset, offset + page_size - 1)

        if args.universities:
            q = q.in_('university_name', args.universities)

        r = q.execute()
        if not r.data:
            break
        all_chunks.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size
        print(f'  取得済み: {offset} 件...', file=sys.stderr)

    if args.limit:
        all_chunks = all_chunks[:args.limit]

    print(f'対象 chunk: {len(all_chunks)} 件\n')

    if args.dry_run:
        print('[DRY-RUN] サンプル（最大5件）:')
        for chunk in all_chunks[:5]:
            text = chunk.get('chunk_text_with_context') or chunk.get('chunk_text', '')
            print(f'  [{chunk["university_name"]}]'
                  f' {chunk.get("unit_name","")}'
                  f' {chunk.get("academic_year","")}'
                  f' | {len(text)}字')
            print(f'    section: {chunk.get("section_path","")[:50]}')
        print(f'\n  推定バッチ数: {len(all_chunks) // args.batch_size + 1}')
        print(f'  Qdrant collection: {QDRANT_COLLECTION}')
        print('[DRY-RUN] 実際の変更は行いません。')
        return

    if not all_chunks:
        print('処理対象がありません。')
        return

    # ── Embedding クライアント初期化 ──────────────────────────
    embed_client = get_embed_client()

    # ── バッチ処理 ────────────────────────────────────────────
    total = len(all_chunks)
    success = 0
    failed = 0
    batch_size = args.batch_size
    total_batches = (total + batch_size - 1) // batch_size

    print(f'{total} 件を {batch_size} 件/バッチで処理中...')

    for batch_start in range(0, total, batch_size):
        batch = all_chunks[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1

        # Embedding 用テキスト
        texts = [
            chunk.get('chunk_text_with_context') or chunk.get('chunk_text', '')
            for chunk in batch
        ]

        print(
            f'  [{batch_num}/{total_batches}] '
            f'{batch_start+1}~{min(batch_start+batch_size, total)} 件... ',
            end='', flush=True
        )

        # Embedding 生成
        embeddings = embed_texts(embed_client, texts)
        if embeddings is None or len(embeddings) != len(batch):
            print('✗ Embedding 失敗')
            failed += len(batch)
            time.sleep(SLEEP_BETWEEN_BATCHES * 3)
            continue

        # Qdrant 用 payload 構築
        chunk_ids = [chunk['id'] for chunk in batch]
        payloads = [
            {
                # Supabase 紐付けキー
                'chunk_id':        chunk['id'],
                'pdf_id':          chunk.get('pdf_id', ''),
                # 検索フィルタ用メタデータ
                'university_name': chunk.get('university_name', ''),
                'unit_name':       chunk.get('unit_name', '') or '',
                'unit_type':       chunk.get('unit_type', '') or '',
                'academic_year':   chunk.get('academic_year', '') or '',
                'pdf_scope':       chunk.get('pdf_scope', '') or '',
                'exam_types':      chunk.get('exam_types', []) or [],
                # 回答生成用（Supabase に取りに戻らなくて済む情報）
                'section_path':    chunk.get('section_path', '') or '',
                'page_number':     chunk.get('page_number', 1) or 1,
                'pdf_url':         chunk.get('pdf_url', '') or '',
                # 検索スニペット用（chunk 本文の先頭200字）
                'chunk_preview':   (chunk.get('chunk_text', '') or '')[:200],
            }
            for chunk in batch
        ]

        # Qdrant に upsert
        try:
            upsert_to_qdrant(qdrant, chunk_ids, embeddings, payloads)
            success += len(batch)
            print(f'✓')
        except Exception as e:
            print(f'✗ Qdrant upsert エラー: {e}')
            failed += len(batch)

        time.sleep(SLEEP_BETWEEN_BATCHES)

    # ── 完了サマリー ──────────────────────────────────────────
    print('\n' + '=' * 65)
    print(f'✅ Phase 4C 完了')
    print(f'  Qdrant 保存成功: {success} 件')
    print(f'  失敗:           {failed} 件')
    print(f'  コレクション:   {QDRANT_COLLECTION}')
    print()
    print('【Qdrant データ確認】')
    try:
        info = qdrant.get_collection(QDRANT_COLLECTION)
        print(f'  総ベクトル数: {info.points_count}')
        print(f'  次元数:       {info.config.params.vectors.size}')
    except Exception:
        pass


if __name__ == '__main__':
    main()