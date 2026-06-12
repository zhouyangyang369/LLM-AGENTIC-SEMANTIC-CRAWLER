# -*- coding: utf-8 -*-
"""
Phase 4D: Retrieval Pipeline

処理フロー:
  ユーザークエリ
    → Layer 1: Query Understanding（LLMでクエリ解析）
    → Layer 2: メタデータフィルタリング（大学名/学部/年度）
    → Layer 3: Hybrid Search（Vector + BM25 + RRF統合）
    → Layer 4: Reranking（CrossEncoder）
    → Layer 5: LLM回答生成（出典明示付き）

使用例:
  from src.pipeline.phase4.retrieval_pipeline import RAGPipeline

  pipeline = RAGPipeline()
  answer = pipeline.query(
      "北海道大学工学部の一般選抜の出願期間はいつですか？"
  )
  print(answer['answer'])
  print(answer['citations'])
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'agentic_crawler')
))

logger = logging.getLogger(__name__)

# ── 設定（.env で上書き可） ──────────────────────────────────────
# Embedding
EMBED_MODEL  = os.environ.get('EMBED_MODEL', 'cohere.embed-multilingual-v3')
INPUT_TYPE_QUERY = 'search_query'   # Cohere: クエリ用
TOP_K_VECTOR = 20
TOP_K_BM25   = 20
TOP_K_RERANK = 5
DEFAULT_YEAR = '令和7年度'

# Qdrant
QDRANT_URL        = os.environ.get('QDRANT_URL', '')
QDRANT_API_KEY    = os.environ.get('QDRANT_API_KEY', '')
QDRANT_COLLECTION = os.environ.get('QDRANT_COLLECTION', 'pdf_chunks')


class QueryParser:
    """ユーザークエリから検索条件を抽出する（Layer 1）"""

    PARSE_PROMPT = """
以下の質問から検索条件を抽出してください。

質問: {query}

JSON形式で回答してください（該当なしは null）:
{{
  "university": "大学名（例：北海道大学）または null",
  "unit": "学部・研究科名（例：工学部、理工学研究科）または null",
  "unit_type": "学部 または 研究科 または null",
  "exam_type": "入試方式（一般選抜/学校推薦型選抜/総合型選抜/社会人/外国人留学生/編入学）または null",
  "topic": "質問のトピック（出願期間/試験科目/配点/出願資格/定員/合格発表等）または null",
  "academic_year": "年度（令和7年度等）または null"
}}"""

    def __init__(self):
        from llm.client import llm_call
        self.llm_call = llm_call

    def parse(self, query: str) -> dict:
        """クエリを解析して検索条件辞書を返す"""
        prompt = self.PARSE_PROMPT.format(query=query)
        try:
            response = self.llm_call(prompt, max_tokens=300)
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                result = json.loads(match.group())
                # academic_year のデフォルト設定
                if not result.get('academic_year'):
                    result['academic_year'] = DEFAULT_YEAR
                return result
        except Exception as e:
            logger.warning('クエリ解析失敗: %s', e)
        return {
            'university': None, 'unit': None, 'unit_type': None,
            'exam_type': None, 'topic': None, 'academic_year': DEFAULT_YEAR
        }


class MetadataFilter:
    """メタデータフィルタリングで候補PDFを絞り込む（Layer 2）"""

    def __init__(self, supabase_client):
        self.client = supabase_client

    def filter_chunks(
        self,
        parsed_query: dict,
        limit: int = 500
    ) -> list[dict]:
        """
        メタデータフィルタで pdf_chunks を絞り込む。
        大学名は必須、それ以外は任意フィルタ。
        """
        q = self.client.table('pdf_chunks')\
            .select('id,university_name,unit_name,academic_year,'
                    'pdf_scope,chunk_text,chunk_context,'
                    'section_path,page_number,pdf_url,exam_types')\
            .limit(limit)

        # 大学名フィルタ（必須）
        university = parsed_query.get('university')
        if university:
            q = q.eq('university_name', university)

        # 年度フィルタ
        academic_year = parsed_query.get('academic_year')
        if academic_year:
            q = q.eq('academic_year', academic_year)

        # 学部/研究科フィルタ
        unit = parsed_query.get('unit')
        if unit:
            q = q.ilike('unit_name', f'%{unit}%')

        # スコープフィルタ
        unit_type = parsed_query.get('unit_type')
        if unit_type == '学部':
            q = q.eq('pdf_scope', 'undergraduate')
        elif unit_type == '研究科':
            q = q.eq('pdf_scope', 'graduate')

        try:
            result = q.execute()
            return result.data or []
        except Exception as e:
            logger.error('メタデータフィルタエラー: %s', e)
            return []


class HybridSearcher:
    """
    Vector（Qdrant Cloud）+ BM25（ローカル）Hybrid Search（Layer 3）
    ベクトルは Qdrant、テキストメタデータは Supabase から取得。
    """

    def __init__(self, supabase_client):
        self.client = supabase_client
        self._embed_client = None
        self._qdrant_client = None

    def _get_embed_client(self):
        if self._embed_client is None:
            import openai
            self._embed_client = openai.OpenAI(
                base_url=os.environ.get(
                    'OPENAI_COMPAT_BASE_URL',
                    'https://ai-jv.vortex.sandisk.com/v1/'
                ),
                api_key=os.environ.get('OPENAI_COMPAT_API_KEY', ''),
            )
        return self._embed_client

    def _get_qdrant_client(self):
        if self._qdrant_client is None:
            from qdrant_client import QdrantClient
            self._qdrant_client = QdrantClient(
                url=QDRANT_URL,
                api_key=QDRANT_API_KEY,
            )
        return self._qdrant_client

    def embed_query(self, query: str) -> Optional[list[float]]:
        """クエリをベクトル化する（Embedding モデル経由）"""
        try:
            client = self._get_embed_client()
            response = client.embeddings.create(
                model=EMBED_MODEL,
                input=[query],
                extra_body={'input_type': INPUT_TYPE_QUERY},
            )
            return response.data[0].embedding
        except Exception:
            try:
                client = self._get_embed_client()
                response = client.embeddings.create(
                    model=EMBED_MODEL,
                    input=[query],
                )
                return response.data[0].embedding
            except Exception as e:
                logger.error('クエリ Embedding エラー: %s', e)
                return None

    def vector_search(
        self,
        query_embedding: list[float],
        parsed_query: dict,
        top_k: int = TOP_K_VECTOR
    ) -> list[dict]:
        """
        Qdrant Cloud でベクトル検索する。
        parsed_query のメタデータで Qdrant payload フィルタも適用。
        Returns: Qdrant の payload リスト（chunk_id 含む）
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        # Qdrant payload フィルタ構築
        conditions = []
        if parsed_query.get('university'):
            conditions.append(
                FieldCondition(
                    key='university_name',
                    match=MatchValue(value=parsed_query['university'])
                )
            )
        if parsed_query.get('academic_year'):
            conditions.append(
                FieldCondition(
                    key='academic_year',
                    match=MatchValue(value=parsed_query['academic_year'])
                )
            )
        if parsed_query.get('unit_type') == '学部':
            conditions.append(
                FieldCondition(
                    key='pdf_scope',
                    match=MatchValue(value='undergraduate')
                )
            )
        elif parsed_query.get('unit_type') == '研究科':
            conditions.append(
                FieldCondition(
                    key='pdf_scope',
                    match=MatchValue(value='graduate')
                )
            )

        qdrant_filter = Filter(must=conditions) if conditions else None

        try:
            qdrant = self._get_qdrant_client()
            results = qdrant.search(
                collection_name=QDRANT_COLLECTION,
                query_vector=query_embedding,
                query_filter=qdrant_filter,
                limit=top_k,
                with_payload=True,
            )
            # payload を返す（chunk_id で Supabase から本文を取得可能）
            return [
                {
                    'id': hit.payload.get('chunk_id', ''),
                    'score': hit.score,
                    **hit.payload,
                }
                for hit in results
            ]
        except Exception as e:
            logger.error('Qdrant 検索エラー: %s', e)
            return []

    def bm25_search(
        self,
        query: str,
        candidates: list[dict],
        top_k: int = TOP_K_BM25
    ) -> list[tuple[str, float]]:
        """
        BM25 スコアで候補をランキングする。
        Returns: [(chunk_id, score), ...]
        """
        if not candidates:
            return []
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.warning('rank_bm25 未インストール。pip install rank-bm25 で導入してください。')
            return [(c['id'], 1.0) for c in candidates[:top_k]]

        # 簡易日本語トークナイズ（文字 n-gram）
        def tokenize(text: str) -> list[str]:
            text = text or ''
            # 2文字 bigram + スペース区切り
            tokens = [text[i:i+2] for i in range(len(text)-1)]
            tokens += text.split()
            return tokens

        corpus = [
            tokenize(c.get('chunk_text_with_context') or c.get('chunk_text', ''))
            for c in candidates
        ]
        query_tokens = tokenize(query)

        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(query_tokens)

        ranked = sorted(
            zip([c['id'] for c in candidates], scores),
            key=lambda x: -x[1]
        )
        return ranked[:top_k]

    def rrf_fusion(
        self,
        vector_ranked: list[str],
        bm25_ranked: list[tuple[str, float]],
        candidates: list[dict],
        k: int = 60,
        top_k: int = TOP_K_VECTOR
    ) -> list[dict]:
        """
        Reciprocal Rank Fusion で Vector と BM25 のランキングを統合。
        Returns: 統合されたchunkリスト
        """
        scores: dict[str, float] = {}

        for rank, chunk_id in enumerate(vector_ranked):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + rank + 1)

        for rank, (chunk_id, _) in enumerate(bm25_ranked):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + rank + 1)

        # スコアでソート
        sorted_ids = sorted(scores, key=lambda x: -scores[x])[:top_k]

        # chunk 辞書を ID でルックアップ
        id_to_chunk = {c['id']: c for c in candidates}
        return [
            {**id_to_chunk[cid], 'rrf_score': scores[cid]}
            for cid in sorted_ids
            if cid in id_to_chunk
        ]


class Reranker:
    """CrossEncoder Reranking（Layer 4）"""

    def __init__(self):
        self._model = None

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(
                    'cross-encoder/ms-marco-MiniLM-L-12-v2',
                    max_length=512
                )
                logger.info('CrossEncoder モデルをロードしました')
            except ImportError:
                logger.warning(
                    'sentence-transformers 未インストール。'
                    'pip install sentence-transformers で導入してください。'
                    'Reranking をスキップします。'
                )
        return self._model

    def rerank(
        self,
        query: str,
        chunks: list[dict],
        top_k: int = TOP_K_RERANK
    ) -> list[dict]:
        """CrossEncoder でチャンクを再ランキング"""
        model = self._get_model()
        if not model or not chunks:
            return chunks[:top_k]

        try:
            import numpy as np
            pairs = [
                (query, c.get('chunk_text', '')[:400])
                for c in chunks
            ]
            scores = model.predict(pairs)
            ranked_indices = np.argsort(scores)[::-1][:top_k]
            return [
                {**chunks[i], 'rerank_score': float(scores[i])}
                for i in ranked_indices
            ]
        except Exception as e:
            logger.warning('Reranking エラー: %s', e)
            return chunks[:top_k]


class AnswerGenerator:
    """LLM 回答生成（出典明示付き）（Layer 5）"""

    ANSWER_PROMPT = """以下の参考資料を基に、質問に日本語で正確に回答してください。
回答の末尾に必ず出典を記載してください。
参考資料に情報がない場合は「提供された資料には該当情報が見当たりませんでした」と回答してください。

【質問】
{query}

【参考資料】
{context}

【回答形式】
（回答本文）

【出典】
（出典リスト）"""

    def __init__(self):
        from llm.client import llm_call
        self.llm_call = llm_call

    def generate(
        self,
        query: str,
        chunks: list[dict]
    ) -> dict:
        """
        コンテキストから回答を生成する。
        Returns: {answer, citations, chunks_used}
        """
        if not chunks:
            return {
                'answer': '関連する情報が見つかりませんでした。',
                'citations': [],
                'chunks_used': 0,
            }

        # コンテキスト構築
        context_parts = []
        citations = []
        for i, chunk in enumerate(chunks, 1):
            univ = chunk.get('university_name', '')
            unit = chunk.get('unit_name', '') or ''
            year = chunk.get('academic_year', '')
            url = chunk.get('pdf_url', '')
            page = chunk.get('page_number', '')
            section = chunk.get('section_path', '') or ''
            text = chunk.get('chunk_text', '')

            context_parts.append(
                f'[資料{i}] {univ} {unit} {year}\n'
                f'セクション: {section}\n'
                f'{text}'
            )
            citations.append({
                'index': i,
                'university_name': univ,
                'unit_name': unit,
                'academic_year': year,
                'section_path': section,
                'page_number': page,
                'pdf_url': url,
            })

        context = '\n\n---\n\n'.join(context_parts)
        prompt = self.ANSWER_PROMPT.format(
            query=query,
            context=context
        )

        try:
            answer = self.llm_call(prompt, max_tokens=1000)
        except Exception as e:
            answer = f'回答生成エラー: {e}'

        return {
            'answer': answer,
            'citations': citations,
            'chunks_used': len(chunks),
        }


class RAGPipeline:
    """
    Phase 4D メインパイプライン。
    全レイヤーを統合して質問に回答する。
    """

    def __init__(self):
        from src.db.supabase_client import get_supabase
        from dotenv import load_dotenv
        load_dotenv()

        self.supabase = get_supabase()
        self.query_parser = QueryParser()
        self.metadata_filter = MetadataFilter(self.supabase)
        self.hybrid_searcher = HybridSearcher(self.supabase)
        self.reranker = Reranker()
        self.answer_generator = AnswerGenerator()

    def query(
        self,
        user_query: str,
        verbose: bool = False
    ) -> dict:
        """
        ユーザークエリに対して回答を生成する。

        Args:
            user_query: 日本語の質問文
            verbose: 詳細ログ出力

        Returns:
            {
                'answer': str,          # 回答本文
                'citations': list,      # 出典リスト
                'chunks_used': int,     # 使用したchunk数
                'parsed_query': dict,   # 解析されたクエリ条件
                'candidates_found': int # メタデータフィルタ後の候補数
            }
        """
        logger.info('Query: %s', user_query)

        # ── Layer 1: クエリ解析 ──────────────────────────────
        parsed = self.query_parser.parse(user_query)
        if verbose:
            print(f'[Layer 1] 解析結果: {json.dumps(parsed, ensure_ascii=False)}')

        # ── Layer 2: メタデータフィルタ ──────────────────────
        candidates = self.metadata_filter.filter_chunks(parsed)
        if verbose:
            print(f'[Layer 2] 候補 chunk 数: {len(candidates)}')

        if not candidates:
            return {
                'answer': f'"{parsed.get("university", "")}" の関連情報が見つかりませんでした。'
                          'データベースに該当大学のデータが存在するか確認してください。',
                'citations': [],
                'chunks_used': 0,
                'parsed_query': parsed,
                'candidates_found': 0,
            }

        # ── Layer 3: Hybrid Search ────────────────────────────
        # Vector 検索
        query_embedding = self.hybrid_searcher.embed_query(user_query)
        candidate_ids = [c['id'] for c in candidates]

        if query_embedding:
            vector_results = self.hybrid_searcher.vector_search(
                query_embedding, candidate_ids
            )
            vector_ranked = [r.get('id', '') for r in vector_results]
        else:
            vector_ranked = candidate_ids[:TOP_K_VECTOR]

        # BM25 検索
        bm25_ranked = self.hybrid_searcher.bm25_search(
            user_query, candidates
        )

        # RRF 統合
        hybrid_results = self.hybrid_searcher.rrf_fusion(
            vector_ranked, bm25_ranked, candidates
        )
        if verbose:
            print(f'[Layer 3] Hybrid Search 結果: {len(hybrid_results)} chunk')

        # ── Layer 4: Reranking ────────────────────────────────
        reranked = self.reranker.rerank(user_query, hybrid_results)
        if verbose:
            print(f'[Layer 4] Reranking 後: {len(reranked)} chunk')
            for i, c in enumerate(reranked, 1):
                score = c.get('rerank_score', c.get('rrf_score', 0))
                print(f'  {i}. [{c["university_name"]}] '
                      f'{c.get("section_path","")[:40]} (score={score:.4f})')

        # ── Layer 5: LLM 回答生成 ─────────────────────────────
        result = self.answer_generator.generate(user_query, reranked)
        result['parsed_query'] = parsed
        result['candidates_found'] = len(candidates)

        return result


# ── CLI インターフェース ───────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Phase 4D: RAG 検索')
    parser.add_argument('query', help='検索クエリ（日本語）')
    parser.add_argument('--verbose', '-v', action='store_true', help='詳細出力')
    args = parser.parse_args()

    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    pipeline = RAGPipeline()
    result = pipeline.query(args.query, verbose=args.verbose)

    print('\n' + '=' * 65)
    print('【回答】')
    print(result['answer'])
    print('\n【検索条件】')
    print(json.dumps(result['parsed_query'], ensure_ascii=False, indent=2))
    print(f'\n候補chunk数: {result["candidates_found"]} → 使用: {result["chunks_used"]} chunk')


if __name__ == '__main__':
    main()