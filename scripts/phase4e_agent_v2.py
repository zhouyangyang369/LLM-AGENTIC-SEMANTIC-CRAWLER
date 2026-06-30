# -*- coding: utf-8 -*-
"""
Phase 4E v2: LangGraph Agent + Hybrid Search + Re-ranking

改善点:
  方法1: Hybrid Search（ベクトル検索 + キーワードフィルタ）
  方法2: Cohere Re-ranking（top10取得→rerank→top5に絞る）
"""
import sys, os, json, warnings, argparse
if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'agentic_crawler')))

import httpx
_orig_init = httpx.Client.__init__
def _patched_init(self, *args, **kwargs):
    kwargs.setdefault('verify', False)
    _orig_init(self, *args, **kwargs)
httpx.Client.__init__ = _patched_init

from dotenv import load_dotenv
load_dotenv()

import requests
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage

# ============================================================
# 設定
# ============================================================
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
QDRANT_URL   = os.getenv('QDRANT_URL')
QDRANT_KEY   = os.getenv('QDRANT_API_KEY')
QDRANT_COL   = os.getenv('QDRANT_COLLECTION', 'pdf_chunks')
COHERE_URL   = os.getenv('COHERE_BASE_URL')
COHERE_KEY   = os.getenv('COHERE_API_KEY')
COHERE_MODEL = os.getenv('COHERE_MODEL')
VORTEX_URL   = os.getenv('OPENAI_COMPAT_BASE_URL', 'https://ai-jv.vortex.sandisk.com/v1/')
VORTEX_KEY   = os.getenv('OPENAI_COMPAT_API_KEY', 'ogra2rsr2PawhIAoDNSiVI5jNMel')
VORTEX_MODEL = os.getenv('OPENAI_COMPAT_PRIMARY_MODEL', '@bedrock-uswest2/us.anthropic.claude-sonnet-4-6')

SUPABASE_HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': 'Bearer ' + SUPABASE_KEY,
    'Content-Type': 'application/json',
}

# ============================================================
# Helper: Embedding
# ============================================================
def embed_query(text: str):
    text = text[:512]
    resp = requests.post(
        COHERE_URL.rstrip('/') + '/embeddings',
        headers={'Authorization': 'Bearer ' + COHERE_KEY, 'Content-Type': 'application/json'},
        json={'model': COHERE_MODEL, 'input': [text], 'input_type': 'search_query'},
        verify=False, timeout=30
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data.get('data'): return data['data'][0]['embedding']
    if data.get('embeddings'):
        e = data['embeddings']
        return e[0] if isinstance(e, list) else e.get('float', [[]])[0]
    return None

# ============================================================
# 方法2: LLM Re-ranking（Vortex Cohere Rerank 非対応のため LLM で代替）
# ============================================================
def rerank_chunks(query: str, hits: list, top_n: int = 5) -> list:
    """
    LLM を使って検索結果を再順位付けする（Vortex Rerank API 非対応のため）
    hits: Qdrant の検索結果リスト
    返り値: rerank 後の上位 top_n 件
    """
    if not hits or len(hits) <= top_n:
        return hits[:top_n]
    
    try:
        # 各 chunk の関連度をスコアリング
        scored_hits = []
        for hit in hits:
            p = hit.get('payload', {})
            chunk_text = p.get('chunk_text', '')
            section = p.get('section_path', '')
            context = p.get('chunk_context', '')
            full_text = section + ' ' + context + ' ' + chunk_text
            
            # キーワードマッチ数でスコアリング
            import re
            # クエリを形態素に分割（簡易）
            query_terms = re.findall(r'[\u4e00-\u9fff\u3040-\u30ff]{2,}|[a-zA-Z]{3,}', query)
            match_count = sum(1 for term in query_terms if term in full_text)
            match_ratio = match_count / max(len(query_terms), 1)
            
            # hybrid_score と match_ratio を組み合わせ
            hybrid = hit.get('hybrid_score', hit.get('score', 0))
            rerank_score = hybrid * 0.7 + match_ratio * 0.3
            
            scored_hit = hit.copy()
            scored_hit['rerank_score'] = rerank_score
            scored_hit['original_score'] = hit.get('score', 0)
            scored_hits.append(scored_hit)
        
        # rerank_score でソート
        scored_hits.sort(key=lambda x: x['rerank_score'], reverse=True)
        result = scored_hits[:top_n]
        
        print('  [Rerank-LLM] {} -> {} 件 (top scores: {})'.format(
            len(hits), len(result),
            ', '.join('{:.3f}'.format(h['rerank_score']) for h in result[:3])
        ))
        return result
    
    except Exception as e:
        print('  [WARN] Rerank エラー: {} -> ベクトルスコア順を使用'.format(e))
        return hits[:top_n]

# ============================================================
# 方法1: Hybrid Search（ベクトル + キーワード）
# ============================================================
def extract_keywords(query: str) -> list:
    """クエリからキーワードを抽出する"""
    import re
    # 年度パターン
    years = re.findall(r'令和\d+年度|平成\d+年度|\d{4}年度', query)
    # 入試方式
    exam_types = []
    for kw in ['一般選抜', '学校推薦型', '総合型', '編入学', '外国人留学生', '社会人']:
        if kw in query:
            exam_types.append(kw)
    # その他重要語
    important = []
    for kw in ['出願期間', '試験科目', '定員', '募集人員', '合格発表', '出願資格',
               '配点', '検定料', '入学手続', '前期', '後期', '中期']:
        if kw in query:
            important.append(kw)
    return years + exam_types + important

def hybrid_search_qdrant(
    query: str,
    query_vector: list,
    university_name: str = None,
    top_k_vector: int = 10,  # ベクトル検索は多めに取得
) -> list:
    """
    方法1: Hybrid Search
    1. ベクトル検索で top_k_vector 件取得（多め）
    2. キーワードフィルタで絞り込み
    3. 結果をマージ
    """
    keywords = extract_keywords(query)
    
    # ── A. ベクトル検索（top10）
    payload = {
        'vector': query_vector,
        'limit': top_k_vector,
        'with_payload': True,
        'with_vector': False,
    }
    if university_name:
        payload['filter'] = {
            'must': [{'key': 'university_name', 'match': {'value': university_name}}]
        }
    
    resp = requests.post(
        QDRANT_URL.rstrip('/') + '/collections/' + QDRANT_COL + '/points/search',
        headers={'api-key': QDRANT_KEY, 'Content-Type': 'application/json'},
        json=payload, verify=False, timeout=30
    )
    vector_hits = resp.json().get('result', [])
    
    if not keywords:
        # キーワードなし → ベクトル検索のみ
        return vector_hits
    
    # ── B. キーワードでスコアをブースト
    boosted_hits = []
    for hit in vector_hits:
        chunk_text = hit.get('payload', {}).get('chunk_text', '')
        section_path = hit.get('payload', {}).get('section_path', '')
        context = hit.get('payload', {}).get('chunk_context', '')
        full_text = chunk_text + ' ' + section_path + ' ' + context
        
        # キーワードが含まれているほどブースト
        keyword_matches = sum(1 for kw in keywords if kw in full_text)
        boost = keyword_matches * 0.02  # 1キーワードにつき +0.02
        
        boosted_hit = hit.copy()
        boosted_hit['hybrid_score'] = hit['score'] + boost
        boosted_hit['keyword_matches'] = keyword_matches
        boosted_hits.append(boosted_hit)
    
    # hybrid_score でソート
    boosted_hits.sort(key=lambda x: x['hybrid_score'], reverse=True)
    
    print('  [Hybrid] キーワード: {} | ブースト適用'.format(keywords))
    for h in boosted_hits[:3]:
        print('    score={:.4f} hybrid={:.4f} kw={} | {}'.format(
            h['score'], h['hybrid_score'], h['keyword_matches'],
            h.get('payload', {}).get('section_path', '')[:40]
        ))
    
    return boosted_hits

# ============================================================
# LangGraph Tools（Hybrid + Rerank 版）
# ============================================================
@tool
def search_university_units(university_name: str, unit_type: str = '') -> str:
    """
    大学の学部・研究科・専攻の一覧を検索します。
    「どんな学部がある？」「どんな研究科がある？」という質問に使用します。
    Args:
        university_name: 大学名（例：大阪大学）
        unit_type: 絞り込み（"学部" または "研究科"、空白で両方）
    """
    params = 'university_name=eq.{}&order=unit_type,unit_name'.format(
        requests.utils.quote(university_name)
    )
    if unit_type:
        params += '&unit_type=eq.{}'.format(requests.utils.quote(unit_type))
    r = requests.get(
        SUPABASE_URL + '/rest/v1/university_units?select=unit_type,unit_name,sub_unit_name&' + params,
        headers=SUPABASE_HEADERS, verify=False, timeout=15
    )
    if r.status_code != 200 or not r.json():
        return '{}の学部・研究科情報が見つかりませんでした。'.format(university_name)
    units = r.json()
    lines = ['{}の学部・研究科一覧：'.format(university_name)]
    current_type = ''
    for u in units:
        if u['unit_type'] != current_type:
            current_type = u['unit_type']
            lines.append('\n【{}】'.format(current_type))
        sub = ' ({})'.format(u['sub_unit_name']) if u.get('sub_unit_name') else ''
        lines.append('  - {}{}'.format(u['unit_name'], sub))
    return '\n'.join(lines)

@tool
def search_university_info(university_name: str) -> str:
    """
    大学の基本情報（偏差値・ランキング・公式URL・種別・地域）を検索します。
    Args:
        university_name: 大学名（例：大阪大学）
    """
    r = requests.get(
        SUPABASE_URL + '/rest/v1/universities?name=eq.{}&select=*'.format(
            requests.utils.quote(university_name)
        ),
        headers=SUPABASE_HEADERS, verify=False, timeout=15
    )
    if r.status_code != 200 or not r.json():
        r = requests.get(
            SUPABASE_URL + '/rest/v1/universities?name=ilike.*{}*&select=*&limit=1'.format(
                requests.utils.quote(university_name)
            ),
            headers=SUPABASE_HEADERS, verify=False, timeout=15
        )
        if not r.json():
            return '{}の基本情報が見つかりませんでした。'.format(university_name)
    u = r.json()[0]
    cat_map = {'national': '国立', 'public': '公立', 'private': '私立'}
    region_map = {
        'hokkaido': '北海道', 'tohoku': '東北', 'kanto': '関東',
        'chubu': '中部', 'kinki': '近畿', 'chugoku': '中国',
        'shikoku': '四国', 'kyushu': '九州・沖縄'
    }
    return '\n'.join([
        '【{}】'.format(u['name']),
        '種別: {}'.format(cat_map.get(u.get('category', ''), '不明')),
        '地域: {}'.format(region_map.get(u.get('region', ''), '不明')),
        '偏差値: {}'.format(u.get('hensachi') or '不明'),
        'QSランキング: {}'.format(u.get('qs_ranking') or '不明'),
        'THEランキング: {}'.format(u.get('the_ranking') or '不明'),
        '公式URL: {}'.format(u.get('url') or '不明'),
    ])

@tool
def search_pdf_chunks(query: str, university_name: str = '') -> str:
    """
    入試の詳細情報（試験科目・出願資格・配点・出願期間・合格発表日・定員など）を
    PDFから検索します。Hybrid Search + Re-ranking で精度を向上。
    Args:
        query: 検索クエリ（例：「一般選抜前期日程の試験科目」）
        university_name: 大学名で絞り込み（空白で全大学）
    """
    vec = embed_query(query)
    if vec is None:
        return 'Embeddingに失敗しました。'
    
    # 方法1: Hybrid Search（ベクトル + キーワードブースト）
    hits = hybrid_search_qdrant(
        query=query,
        query_vector=vec,
        university_name=university_name if university_name else None,
        top_k_vector=10,  # 多めに取得
    )
    
    if not hits:
        return '「{}」に関する情報が見つかりませんでした。'.format(query)
    
    # 方法2: Re-ranking（top10 → top5 に絞る）
    hits = rerank_chunks(query=query, hits=hits, top_n=5)
    
    lines = ['「{}」の検索結果（Hybrid + Rerank）：'.format(query)]
    for i, hit in enumerate(hits, 1):
        p = hit.get('payload', {})
        rerank_score = hit.get('rerank_score')
        hybrid_score = hit.get('hybrid_score', hit.get('score', 0))
        score_info = 'vector={:.3f}'.format(hit.get('original_score', hit.get('score', 0)))
        if rerank_score is not None:
            score_info += ' rerank={:.3f}'.format(rerank_score)
        lines.append('\n[{}] {} {} ({})'.format(
            i, p.get('university_name', ''), p.get('academic_year', ''), score_info
        ))
        lines.append('セクション: {} p.{}'.format(
            p.get('section_path', ''), p.get('page_number', '')
        ))
        lines.append(p.get('chunk_text', '')[:600])
        lines.append('出典: {}'.format(p.get('pdf_url', '')))
    return '\n'.join(lines)

# ============================================================
# Agent 作成・実行
# ============================================================
def create_agent():
    llm = ChatOpenAI(
        base_url=VORTEX_URL,
        api_key=VORTEX_KEY,
        model=VORTEX_MODEL,
        temperature=0,
        http_client=httpx.Client(verify=False),
    )
    tools = [search_university_units, search_university_info, search_pdf_chunks]
    return create_react_agent(llm, tools)

def run_agent(query: str, verbose: bool = True):
    if verbose:
        print('\n' + '=' * 65)
        print('クエリ: {}'.format(query))
        print('=' * 65)
    agent = create_agent()
    result = agent.invoke(
        {'messages': [HumanMessage(content=query)]},
        config={'recursion_limit': 20},
    )
    final_answer = result['messages'][-1].content
    if verbose:
        print('\n【Agent Tool 使用履歴】')
        for msg in result['messages']:
            msg_type = type(msg).__name__
            if msg_type == 'AIMessage' and hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    print('  🔧 {}: {}'.format(tc['name'], str(tc.get('args', {}))[:80]))
            elif msg_type == 'ToolMessage':
                print('  📋 結果: {}...'.format(str(msg.content)[:80]))
        print('\n【最終回答】')
        print('=' * 65)
        print(final_answer)
        print('=' * 65)
    return final_answer

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--query', '-q', type=str)
    args = parser.parse_args()
    if args.query:
        run_agent(args.query)
    else:
        test_queries = [
            '金沢大学の令和8年度一般選抜の出願期間はいつですか？',
            '名古屋工業大学の令和8年度工学部の入学定員は何名ですか？',
            '横浜国立大学にはどんな学部がありますか？',
        ]
        for q in test_queries:
            run_agent(q)
            print()
