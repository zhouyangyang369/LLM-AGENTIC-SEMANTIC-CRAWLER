# -*- coding: utf-8 -*-
"""
RAG API サーバー v3（FastAPI + LangGraph Agent + Hybrid Search + Re-ranking）

起動方法:
  python scripts/rag_api_server.py
  → http://localhost:8000
"""
import sys, os, json, time, warnings, re
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
import urllib3
urllib3.disable_warnings()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

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

app = FastAPI(title='Nyushi RAG API', version='3.0.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# ============================================================
# Request モデル
# ============================================================
class ChatRequest(BaseModel):
    message: str
    locale: str = 'ja'
    university_name: Optional[str] = None
    history: List[dict] = []

class SearchRequest(BaseModel):
    query: str
    university_name: Optional[str] = None
    top_k: int = 5

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
# 方法1: Hybrid Search
# ============================================================
def extract_keywords(query: str) -> list:
    years = re.findall(r'令和\d+年度|平成\d+年度|\d{4}年度', query)
    exam_types = [kw for kw in ['一般選抜','学校推薦型','総合型','編入学','外国人留学生','社会人'] if kw in query]
    important = [kw for kw in ['出願期間','試験科目','定員','募集人員','合格発表','出願資格',
                               '配点','検定料','入学手続','前期','後期','中期'] if kw in query]
    return years + exam_types + important

def hybrid_search_qdrant(query: str, query_vector: list, university_name: str = None, top_k: int = 10) -> list:
    payload = {
        'vector': query_vector, 'limit': top_k,
        'with_payload': True, 'with_vector': False,
    }
    if university_name:
        payload['filter'] = {'must': [{'key': 'university_name', 'match': {'value': university_name}}]}
    resp = requests.post(
        QDRANT_URL.rstrip('/') + '/collections/' + QDRANT_COL + '/points/search',
        headers={'api-key': QDRANT_KEY, 'Content-Type': 'application/json'},
        json=payload, verify=False, timeout=30
    )
    hits = resp.json().get('result', [])
    keywords = extract_keywords(query)
    if not keywords:
        return hits
    boosted = []
    for hit in hits:
        p = hit.get('payload', {})
        full_text = ' '.join([
            p.get('chunk_text', ''), p.get('section_path', ''), p.get('chunk_context', '')
        ])
        kw_matches = sum(1 for kw in keywords if kw in full_text)
        h = hit.copy()
        h['hybrid_score'] = hit['score'] + kw_matches * 0.02
        h['keyword_matches'] = kw_matches
        boosted.append(h)
    boosted.sort(key=lambda x: x['hybrid_score'], reverse=True)
    return boosted

# ============================================================
# 方法2: LLM Re-ranking
# ============================================================
def rerank_chunks(query: str, hits: list, top_n: int = 5) -> list:
    if not hits or len(hits) <= top_n:
        return hits[:top_n]
    query_terms = re.findall(r'[\u4e00-\u9fff\u3040-\u30ff]{2,}|[a-zA-Z]{3,}', query)
    scored = []
    for hit in hits:
        p = hit.get('payload', {})
        full_text = ' '.join([p.get('chunk_text',''), p.get('section_path',''), p.get('chunk_context','')])
        match_count = sum(1 for term in query_terms if term in full_text)
        match_ratio = match_count / max(len(query_terms), 1)
        hybrid = hit.get('hybrid_score', hit.get('score', 0))
        h = hit.copy()
        h['rerank_score'] = hybrid * 0.7 + match_ratio * 0.3
        h['original_score'] = hit.get('score', 0)
        scored.append(h)
    scored.sort(key=lambda x: x['rerank_score'], reverse=True)
    return scored[:top_n]

# ============================================================
# LangGraph Tools
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
    「偏差値は？」「国立？私立？」「公式サイトは？」という質問に使用します。
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
    現在対応している大学：山形大学・大阪大学・福島大学・横浜国立大学・名古屋工業大学・
    上越教育大学・旭川医科大学・北見工業大学・東京外国語大学・金沢大学
    Args:
        query: 検索クエリ
        university_name: 大学名で絞り込み（空白で全大学）
    """
    vec = embed_query(query)
    if vec is None:
        return 'Embeddingに失敗しました。'
    # 方法1: Hybrid Search
    hits = hybrid_search_qdrant(
        query=query, query_vector=vec,
        university_name=university_name if university_name else None,
        top_k=10
    )
    if not hits:
        return '「{}」に関する情報が見つかりませんでした。'.format(query)
    # 方法2: Re-ranking
    hits = rerank_chunks(query=query, hits=hits, top_n=5)
    lines = ['「{}」の検索結果：'.format(query)]
    for i, hit in enumerate(hits, 1):
        p = hit.get('payload', {})
        rerank_score = hit.get('rerank_score', hit.get('score', 0))
        lines.append('\n[{}] {} {} (score={:.3f})'.format(
            i, p.get('university_name', ''), p.get('academic_year', ''), rerank_score
        ))
        lines.append('セクション: {} p.{}'.format(p.get('section_path', ''), p.get('page_number', '')))
        lines.append(p.get('chunk_text', '')[:600])
        lines.append('出典: {}'.format(p.get('pdf_url', '')))
    return '\n'.join(lines)

# ============================================================
# LangGraph Agent
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

# ============================================================
# API エンドポイント
# ============================================================
@app.get('/health')
def health():
    return {'status': 'ok', 'version': '3.0.0', 'mode': 'LangGraph Agent + Hybrid + Rerank'}

@app.post('/api/search')
def api_search(req: SearchRequest):
    try:
        vec = embed_query(req.query)
        if vec is None:
            return {'error': 'Embedding failed', 'hits': []}
        hits = hybrid_search_qdrant(req.query, vec, req.university_name, req.top_k)
        hits = rerank_chunks(req.query, hits, req.top_k)
        return {
            'hit_count': len(hits),
            'hits': [{
                'score': h.get('rerank_score', h.get('score', 0)),
                'university_name': h['payload'].get('university_name'),
                'academic_year': h['payload'].get('academic_year'),
                'chunk_text': h['payload'].get('chunk_text', '')[:200],
                'pdf_url': h['payload'].get('pdf_url'),
            } for h in hits]
        }
    except Exception as e:
        return {'error': str(e), 'hits': []}

@app.post('/api/chat')
def api_chat(req: ChatRequest):
    def generate():
        try:
            lang_map = {'ja': '日本語で回答してください。', 'zh': '请用中文回答。', 'en': 'Please respond in English.'}
            lang_instruction = lang_map.get(req.locale, lang_map['ja'])
            system_prompt = (
                'You are 入試AIアシスタント, a helpful assistant for Japanese university entrance information.\n'
                '{}\n'
                'Use the available tools to search for accurate information before answering.\n'
                'Always cite sources (university name, academic year, PDF URL) in your answer.\n'
                '※現在のPDFデータは実験対象10大学のみ対応。'
                '他大学は university_units と university_info のみ利用可能。'
            ).format(lang_instruction)

            messages = [SystemMessage(content=system_prompt)]
            for h in (req.history or [])[-6:]:
                if h.get('role') == 'user':
                    messages.append(HumanMessage(content=h.get('content', '')))
                elif h.get('role') == 'assistant':
                    messages.append(AIMessage(content=h.get('content', '')))
            messages.append(HumanMessage(content=req.message))

            agent = create_agent()
            result = agent.invoke(
                {'messages': messages},
                config={'recursion_limit': 20},
            )

            # sources・tools_used を収集
            sources = []
            tool_calls_info = []
            for msg in result['messages']:
                msg_type = type(msg).__name__
                if msg_type == 'AIMessage' and hasattr(msg, 'tool_calls') and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_calls_info.append(tc['name'])
                elif msg_type == 'ToolMessage':
                    content = str(msg.content)
                    urls = re.findall(r'https?://[\S]+\.pdf', content)
                    for url in urls:
                        univ_match = re.search(r'\[\d+\] ([\S]+大学)', content)
                        label = univ_match.group(1) if univ_match else 'PDF'
                        if url not in [s.get('url') for s in sources]:
                            sources.append({'label': label, 'url': url})

            if sources:
                yield 'data: {}\n\n'.format(json.dumps({'sources': sources[:5]}, ensure_ascii=False))
            if tool_calls_info:
                yield 'data: {}\n\n'.format(json.dumps(
                    {'tools_used': list(dict.fromkeys(tool_calls_info))},
                    ensure_ascii=False
                ))

            # 最終回答をストリーミング送信
            final_answer = result['messages'][-1].content
            chunk_size = 5
            for i in range(0, len(final_answer), chunk_size):
                chunk = final_answer[i:i+chunk_size]
                yield 'data: {}\n\n'.format(json.dumps({'token': chunk}, ensure_ascii=False))
                time.sleep(0.01)

            yield 'data: [DONE]\n\n'

        except Exception as e:
            error_msg = '\n\n⚠️ エラーが発生しました: {}'.format(str(e))
            yield 'data: {}\n\n'.format(json.dumps({'token': error_msg}, ensure_ascii=False))
            yield 'data: [DONE]\n\n'

    return StreamingResponse(
        generate(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'},
    )

# ============================================================
# 起動
# ============================================================
if __name__ == '__main__':
    import uvicorn
    print('=' * 60)
    print('Nyushi RAG API v3.0（LangGraph + Hybrid + Rerank）')
    print('=' * 60)
    print('URL: http://localhost:8000')
    print('Health: http://localhost:8000/health')
    print('=' * 60)
    uvicorn.run(app, host='0.0.0.0', port=8000, log_level='info')
