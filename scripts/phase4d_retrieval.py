# -*- coding: utf-8 -*-
"""
Phase 4D: RAG 検索・回答生成パイプライン

処理フロー:
  1. Query Understanding  - LLMでクエリから検索条件（大学名・年度・入試方式）を抽出
  2. Qdrant Vector Search - 同じCohereモデルでクエリをembedding→ベクトル検索+payloadフィルタ
  3. LLM 回答生成         - 検索結果をcontextにLLMで回答生成（出典付き）

使用方法:
  # 対話モード
  python scripts/phase4d_retrieval.py

  # 単発クエリ
  python scripts/phase4d_retrieval.py --query "金沢大学の令和8年度一般選抜の出願期間は？"

  # 大学を指定
  python scripts/phase4d_retrieval.py --query "推薦入試の定員は？" --university 北見工業大学

  # 検索結果のみ表示（LLM回答なし）
  python scripts/phase4d_retrieval.py --query "出願期間" --search-only
"""
import sys
import os
import json
import argparse
from typing import Optional

if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'agentic_crawler')
))

# SSL証明書検証を無効化（社内ネットワーク自己署名証明書対応）
import ssl
import httpx
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# httpx のグローバルSSL設定を上書き
_orig_init = httpx.Client.__init__
def _patched_init(self, *args, **kwargs):
    kwargs.setdefault('verify', False)
    _orig_init(self, *args, **kwargs)
httpx.Client.__init__ = _patched_init

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

TOP_K = 5  # 検索結果上位件数

# 実験対象10大学
EXP_UNIVERSITIES = [
    '山形大学', '大阪大学', '福島大学', '横浜国立大学',
    '名古屋工業大学', '上越教育大学', '旭川医科大学',
    '北見工業大学', '東京外国語大学', '金沢大学',
]


# ============================================================
# Embedding（クエリ用）
# ============================================================
def embed_query(text: str) -> Optional[list]:
    """クエリテキストをembeddingする（search_query モード）"""
    import requests
    url = COHERE_BASE_URL.rstrip('/') + '/embeddings'
    headers = {
        'Authorization': 'Bearer ' + COHERE_API_KEY,
        'Content-Type': 'application/json',
    }
    payload = {
        'model': COHERE_MODEL,
        'input': [text],
        'input_type': 'search_query',  # クエリは search_query モード
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if 'data' in data:
            return data['data'][0]['embedding']
        if 'embeddings' in data:
            emb = data['embeddings']
            if isinstance(emb, dict) and 'float' in emb:
                return emb['float'][0]
            if isinstance(emb, list):
                return emb[0]
        return None
    except Exception as e:
        print('  [ERROR] Embedding エラー: {}'.format(e))
        return None


# ============================================================
# Query Understanding（LLMでクエリ解析）
# ============================================================
QUERY_PARSE_PROMPT = """以下の質問から検索条件を抽出してください。

質問: {query}

対象大学リスト（このリストから選ぶ）:
{universities}

以下のJSON形式で出力してください。
情報がない場合は null にしてください。

{{
  "university_name": "大学名（上記リストから選ぶ、不明はnull）",
  "academic_year": "年度（例：令和8年度、不明はnull）",
  "exam_type": "入試方式（例：一般選抜、推薦型、総合型、不明はnull）",
  "search_query": "ベクトル検索用の日本語クエリ（元の質問をそのまま使うか、より検索しやすい形に言い換え）"
}}

JSONのみ出力してください。"""


def parse_query(query: str) -> dict:
    """LLMでクエリから検索条件を抽出する"""
    from llm.client import llm_call

    prompt = QUERY_PARSE_PROMPT.format(
        query=query,
        universities='\n'.join('- ' + u for u in EXP_UNIVERSITIES),
    )
    try:
        response = llm_call(prompt, max_tokens=256)
        # JSON パース
        import re
        response = re.sub(r'^```[a-zA-Z]*\n?', '', response.strip())
        response = re.sub(r'\n?```$', '', response)
        response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()
        result = json.loads(response)
        return result
    except Exception as e:
        print('  [WARN] Query parse エラー: {} -> フォールバック'.format(e))
        return {
            'university_name': None,
            'academic_year': None,
            'exam_type': None,
            'search_query': query,
        }


# ============================================================
# Qdrant 検索
def search_qdrant(
    query_vector: list,
    university_name=None,
    academic_year=None,
    top_k=TOP_K,
):
    """Qdrantでベクトル検索する（requests直接呼び出し・SSL verify無効）。"""
    import requests
    import warnings
    warnings.filterwarnings('ignore', message='Unverified HTTPS')

    must_conditions = []
    if university_name:
        must_conditions.append({
            "key": "university_name",
            "match": {"value": university_name}
        })
    if academic_year:
        must_conditions.append({
            "key": "academic_year",
            "match": {"value": academic_year}
        })

    payload = {
        "vector": query_vector,
        "limit": top_k,
        "with_payload": True,
        "with_vector": False,
    }
    if must_conditions:
        payload["filter"] = {"must": must_conditions}

    url = QDRANT_URL.rstrip('/') + '/collections/' + QDRANT_COLLECTION + '/points/search'
    headers = {
        "api-key": QDRANT_API_KEY,
        "Content-Type": "application/json",
    }

    resp = requests.post(url, headers=headers, json=payload, verify=False, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    from types import SimpleNamespace
    points = []
    for item in data.get('result', []):
        point = SimpleNamespace(
            id=item['id'],
            score=item['score'],
            payload=item.get('payload', {}),
        )
        points.append(point)
    return points



# ============================================================
# Supabase から chunk 本文を取得
# ============================================================
def fetch_chunk_texts(chunk_ids: list, points: list = None) -> dict:
    """Qdrant payload から chunk 情報を取得する（Supabase不要）"""
    if points is None:
        return {}
    result = {}
    for point in points:
        chunk_id = str(point.id)
        payload = point.payload or {}
        result[chunk_id] = {
            'id': chunk_id,
            'chunk_text': payload.get('chunk_text', ''),
            'chunk_context': payload.get('chunk_context', ''),
            'section_path': payload.get('section_path', ''),
            'page_number': payload.get('page_number', 1),
            'pdf_url': payload.get('pdf_url', ''),
            'university_name': payload.get('university_name', ''),
            'academic_year': payload.get('academic_year', ''),
            'unit_name': payload.get('unit_name', ''),
        }
    return result



# ============================================================
# LLM 回答生成
# ============================================================
ANSWER_PROMPT = """あなたは日本の大学入試情報に詳しいアシスタントです。
以下の検索結果を参考に、質問に日本語で回答してください。

質問: {query}

【検索結果】
{context}

【回答ルール】
- 検索結果に基づいて正確に回答してください
- 検索結果に情報がない場合は「情報が見つかりませんでした」と答えてください
- 回答の最後に出典（大学名・年度・PDF URL）を記載してください
- 簡潔かつ具体的に答えてください"""


def generate_answer(query: str, search_results: list, chunk_texts: dict) -> str:
    """検索結果を元にLLMで回答を生成する"""
    from llm.client import llm_call

    # context 構築
    context_parts = []
    for i, point in enumerate(search_results, 1):
        chunk_id = str(point.id)
        payload = point.payload or {}
        chunk_data = chunk_texts.get(chunk_id, {})

        chunk_text = chunk_data.get('chunk_text', payload.get('chunk_text', ''))
        univ = payload.get('university_name', '')
        year = payload.get('academic_year', '')
        section = payload.get('section_path', '')
        page = payload.get('page_number', '')
        url = payload.get('pdf_url', '')

        context_parts.append(
            '[{}] {}{}{}\n'
            'セクション: {} (p.{})\n'
            '{}\n'
            '出典: {}\n'.format(
                i, univ, ' ' + year if year else '',
                ' ' + section if section else '',
                section, page,
                chunk_text[:900],
                url,
            )
        )

    context = '\n---\n'.join(context_parts)

    prompt = ANSWER_PROMPT.format(query=query, context=context)
    try:
        answer = llm_call(prompt, max_tokens=1024)
        return answer.strip()
    except Exception as e:
        return '回答生成エラー: {}'.format(e)


# ============================================================
# メイン検索関数
# ============================================================
def rag_search(
    query: str,
    university_name: Optional[str] = None,
    academic_year: Optional[str] = None,
    search_only: bool = False,
    verbose: bool = True,
) -> dict:
    """RAG検索のメイン関数"""

    if verbose:
        print('\n' + '=' * 65)
        print('クエリ: {}'.format(query))
        print('=' * 65)

    # Step 1: Query Understanding
    if verbose:
        print('\n[Step 1] クエリ解析中...')
    parsed = parse_query(query)
    if verbose:
        print('  大学名  : {}'.format(parsed.get('university_name')))
        print('  年度    : {}'.format(parsed.get('academic_year')))
        print('  入試方式: {}'.format(parsed.get('exam_type')))
        print('  検索用  : {}'.format(parsed.get('search_query')))

    # 引数で指定された場合は優先
    final_university = university_name or parsed.get('university_name')
    final_year = academic_year or parsed.get('academic_year')
    search_text = parsed.get('search_query') or query

    # Step 2: Embedding
    if verbose:
        print('\n[Step 2] クエリをEmbedding中...')
    query_vector = embed_query(search_text)
    if query_vector is None:
        return {'error': 'Embedding失敗'}
    if verbose:
        print('  OK ({}次元)'.format(len(query_vector)))

    # Step 3: Qdrant 検索
    if verbose:
        print('\n[Step 3] Qdrant検索中...')
        print('  フィルタ: university={}, year={}'.format(final_university, final_year))
    points = search_qdrant(query_vector, final_university, final_year)
    if verbose:
        print('  {}件ヒット'.format(len(points)))

    if not points:
        if verbose:
            print('  検索結果なし')
        return {'query': query, 'results': [], 'answer': '該当する情報が見つかりませんでした。'}

    # Step 4: Supabase から全文取得
    chunk_ids = [str(p.id) for p in points]
    chunk_texts = fetch_chunk_texts(chunk_ids, points)

    # 検索結果表示
    if verbose:
        print('\n【検索結果 Top{}】'.format(len(points)))
        for i, point in enumerate(points, 1):
            payload = point.payload or {}
            chunk_data = chunk_texts.get(str(point.id), {})
            print('  [{}/{}] score={:.4f} | {} | {} | p.{}'.format(
                i, len(points),
                point.score,
                payload.get('university_name', ''),
                payload.get('academic_year', ''),
                payload.get('page_number', ''),
            ))
            print('       section: {}'.format(payload.get('section_path', '')[:50]))
            chunk_text = chunk_data.get('chunk_text', payload.get('chunk_text', ''))
            print('       chunk: {}...'.format(chunk_text[:100]))

    if search_only:
        return {'query': query, 'results': points, 'chunk_texts': chunk_texts}

    # Step 5: LLM 回答生成
    if verbose:
        print('\n[Step 4] LLMで回答生成中...')
    answer = generate_answer(query, points, chunk_texts)

    if verbose:
        print('\n' + '=' * 65)
        print('【回答】')
        print('=' * 65)
        print(answer)
        print('=' * 65)

    return {
        'query': query,
        'parsed': parsed,
        'results': points,
        'answer': answer,
    }


# ============================================================
# メイン
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='Phase 4D: RAG 検索・回答生成')
    parser.add_argument('--query', '-q', type=str, help='検索クエリ')
    parser.add_argument('--university', '-u', type=str, help='大学名で絞り込み')
    parser.add_argument('--year', type=str, help='年度で絞り込み（例：令和8年度）')
    parser.add_argument('--search-only', action='store_true', help='検索結果のみ表示（LLM回答なし）')
    parser.add_argument('--top-k', type=int, default=5, help='検索結果件数')
    args = parser.parse_args()

    global TOP_K
    TOP_K = args.top_k

    if args.query:
        # 単発クエリモード
        rag_search(
            query=args.query,
            university_name=args.university,
            academic_year=args.year,
            search_only=args.search_only,
        )
    else:
        # 対話モード
        print('Phase 4D RAG 検索システム（対話モード）')
        print('対象大学: {}'.format(', '.join(EXP_UNIVERSITIES)))
        print('終了するには "exit" または "quit" を入力してください')
        print()

        while True:
            try:
                query = input('質問> ').strip()
            except (EOFError, KeyboardInterrupt):
                print('\n終了します')
                break

            if not query:
                continue
            if query.lower() in ('exit', 'quit', 'q'):
                print('終了します')
                break

            rag_search(
                query=query,
                university_name=args.university,
                academic_year=args.year,
                search_only=args.search_only,
            )


if __name__ == '__main__':
    main()
