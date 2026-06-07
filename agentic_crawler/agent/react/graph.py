"""
ReAct Agent の LangGraph グラフ定義

フロー:
  [start] → prepare（sitemap + トップページ + Tavily で入試 URL 候補を収集）
           → fetch_candidates（候補を確定的に全ページ取得・PDF抽出）
           → agent_node（LLM が不足分を補完）
           → tools_node → collect_pdfs → agent_node  (ループ)
           → finalize → [end]

設計方針:
- prepare: LLM なしで確実に入試関連 URL を集める（3ソース）
- fetch_candidates: LLM なしで全候補ページから PDF を抽出
- LLM は「まだ見つかっていない情報を search_web で補完」するだけ
"""

import sys
import os
import logging
import re
from typing import Annotated

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage, ToolMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agent.react.tools import AGENT_TOOLS, _is_relevant_pdf
from tools.fetcher import extract_pdf_links_from_markdown, fetch_page_as_markdown
from tools.sitemap_parser import discover_sitemap
from tools.tavily_search import tavily_search
from config import (
    LLM_BACKEND,
    OLLAMA_BASE_URL, OLLAMA_PRIMARY_MODEL,
    PORTKEY_API_KEY, PORTKEY_VIRTUAL_KEY_GEMINI, PORTKEY_PRIMARY_MODEL,
    ADMISSION_KEYWORDS_JA,
)

logger = logging.getLogger(__name__)

MAX_STEPS = 15  # LLM 補完フェーズの最大ステップ数

# URL フィルタ用キーワード（英語 + 日本語ローマ字）
_ADMISSION_KW = [k.lower() for k in ADMISSION_KEYWORDS_JA] + [
    "entrance", "nyushi", "nyugaku", "boshu", "for_grad", "for_comp",
    "daigakuin", "graduate", "admis", "exam", "senbatsu",
]

# PDF として不適切なテキストのキーワード（運営・管理系 + 統計・過去問系）
_PDF_EXCLUDE_EXTRA = [
    "中期目標", "中期計画", "年度計画", "経営協議会", "役職員", "監察",
    "情報公開", "ディプロマ", "カリキュラムマップ", "シラバス", "授業料",
    # 統計・結果データ（募集要項ではない）
    "出身所在地別調", "合格者入試成績", "現役・浪人別", "志願者数", "入学者数",
    "最高点", "平均点", "最低点",
    # 過去問・参考資料
    "入学試験問題", "解答例", "過去問",
    # 大学案内・広報
    "大学訪問", "出前講義", "プログラム題目", "印刷用PDF",
    # 手続き書類（募集要項ではない）
    "別紙", "貸出", "Certificate of Eligibility", "クレジット払い",
]

# PDF として有効な正向キーワード（いずれかを含むこと）
_PDF_POSITIVE_KW = [
    "募集要項", "選抜要項", "入学者選抜", "出願要領", "出願要項",
    "学生募集", "入試要項", "Application Guide", "Application for",
    "Guideline", "guideline", "boshu", "youkou", "youryou",
    "編入学", "社会人", "外国人留学生", "研究生", "科目等履修",
]

# URL に含まれる場合も有効と判断するパターン
_PDF_URL_POSITIVE = [
    "youkou", "boshu", "nyushi", "nyugaku", "senbatsu", "shutsugan",
    "admission", "guideline", "bosyu",
]


# ── 状態定義 ─────────────────────────────────────────────────────────

def _add_messages(left: list, right: list) -> list:
    return left + right


class ReactState(BaseModel):
    school_name: str
    official_url: str = ""
    domain: str = ""

    # prepare フェーズで収集した入試関連候補 URL
    candidate_urls: list[str] = Field(default_factory=list)
    # fetch_candidates フェーズで訪問済みの URL
    fetched_urls: set[str] = Field(default_factory=set)

    messages: Annotated[list[BaseMessage], _add_messages] = Field(default_factory=list)
    step_count: int = 0
    is_done: bool = False

    collected_pdfs: list[dict] = Field(default_factory=list)
    visited_urls: set[str] = Field(default_factory=set)

    class Config:
        arbitrary_types_allowed = True


# ── LLM クライアント ─────────────────────────────────────────────────

def _make_llm():
    if LLM_BACKEND == "ollama":
        return ChatOpenAI(
            base_url=OLLAMA_BASE_URL,
            api_key="ollama",
            model=OLLAMA_PRIMARY_MODEL,
            temperature=0.0,
            max_tokens=4096,
        )
    else:
        return ChatOpenAI(
            base_url="https://api.portkey.ai/v1",
            api_key=PORTKEY_API_KEY,
            model=PORTKEY_PRIMARY_MODEL,
            temperature=0.0,
            max_tokens=4096,
            default_headers={
                "x-portkey-api-key": PORTKEY_API_KEY,
                "x-portkey-virtual-key": PORTKEY_VIRTUAL_KEY_GEMINI,
            },
        )


def _make_system_prompt(school_name: str) -> str:
    return f"""/no_think
あなたは日本の大学入試情報収集の補完エージェントです。必ず日本語で回答してください。

【状況】
「{school_name}」の入試関連ページは既にスキャン済みです。
あなたの役割は**まだ見つかっていない募集要項 PDF を補完すること**です。

【収集対象 PDF】
- 学部入試: 一般選抜・推薦型・総合型・外国人留学生 の募集要項
- 大学院: 修士・博士・専門職 の学生募集要項

【ツール】
1. search_web(query) — 見つかっていない PDF を Web 検索で探す
2. fetch_page(url) — 特定ページを確認する
3. report_done(summary) — 補完完了を宣言する

【重要ルール】
- **必ず毎回いずれかのツールを呼び出すこと**
- 学部・大学院の両方の募集要項が揃ったら report_done を呼ぶ
- 同じ URL を重複して fetch_page しない
"""


# ── ノード定義 ────────────────────────────────────────────────────────

def _extract_links_from_markdown(md: str, base_domain: str) -> list[str]:
    """マークダウンからリンクを抽出（同一ドメインのみ）"""
    urls = re.findall(r'https?://[^\s\)\]>\"\']+', md)
    return [u.rstrip('.,)') for u in urls if base_domain in u and not u.endswith('.pdf')]


def node_prepare(state: ReactState) -> dict:
    """
    3つのソースから入試関連 URL を確定的に収集:
    1. Sitemap (キーワードフィルタ)
    2. トップページの ナビリンク（sitemap が役に立たない場合）
    3. Tavily 検索で入試ページを発見
    """
    school = state.school_name
    domain = state.domain or re.sub(r'^https?://', '', state.official_url).split('/')[0]
    candidates: set[str] = set()

    # ── 1. Sitemap ──────────────────────────────────────────
    logger.info(f"[{school}] Prepare: discovering sitemap URLs")
    all_urls = discover_sitemap(state.official_url)
    sitemap_hits = [u for u in all_urls if any(kw in u.lower() for kw in _ADMISSION_KW)]
    candidates.update(sitemap_hits)
    logger.info(f"[{school}] Prepare sitemap: {len(all_urls)} total → {len(sitemap_hits)} keyword hits")

    # ── 2. トップページのナビリンク（sitemap ヒットが少ない場合）──
    if len(sitemap_hits) < 5:
        logger.info(f"[{school}] Sitemap hits low, fetching top page for nav links")
        try:
            top_md = fetch_page_as_markdown(state.official_url)
            nav_links = _extract_links_from_markdown(top_md, domain)
            nav_hits = [u for u in nav_links if any(kw in u.lower() for kw in _ADMISSION_KW)]
            candidates.update(nav_hits)
            logger.info(f"[{school}] Top page nav links: {len(nav_links)} total → {len(nav_hits)} hits")
        except Exception as e:
            logger.warning(f"[{school}] Top page fetch failed: {e}")

    # ── 3. Tavily 検索 ──────────────────────────────────────
    logger.info(f"[{school}] Prepare: Tavily search for admission pages")
    tavily_queries = [
        f"{school} 募集要項 入試 site:{domain}",
        f"{school} 大学院 募集要項 site:{domain}",
    ]
    for q in tavily_queries:
        try:
            results = tavily_search(q, max_results=5)
            for r in results:
                url = r.get("url", "")
                if domain in url and not url.endswith('.pdf'):
                    candidates.add(url)
        except Exception as e:
            logger.warning(f"[{school}] Tavily failed for '{q}': {e}")

    final = list(candidates)[:80]
    logger.info(f"[{school}] Prepare: {len(final)} candidate URLs collected (sitemap+nav+tavily)")
    return {"candidate_urls": final, "domain": domain}


def node_fetch_candidates(state: ReactState) -> dict:
    """
    LLM を使わずに候補 URL を全て取得し PDF を抽出する確定的ノード。
    エージェントの事前作業として最大ページ数を制限する。
    """
    school = state.school_name
    new_pdfs = list(state.collected_pdfs)
    seen_pdf = {p["url"] for p in new_pdfs}
    fetched = set(state.fetched_urls)

    MAX_FETCH = 20  # 確定的フェーズで取得するページ上限

    to_fetch = [u for u in state.candidate_urls if u not in fetched][:MAX_FETCH]
    logger.info(f"[{school}] FetchCandidates: fetching {len(to_fetch)} pages")

    for url in to_fetch:
        try:
            md = fetch_page_as_markdown(url)
            fetched.add(url)
            # PDF リンクを抽出（テキスト + URL 両方を正向フィルタに渡す）
            pdf_links = extract_pdf_links_from_markdown(md)
            for item in pdf_links:
                pdf_url = item.get("url", "")
                pdf_text = item.get("text", "")
                if pdf_url not in seen_pdf and _is_pdf_relevant_extended(pdf_text, pdf_url):
                    seen_pdf.add(pdf_url)
                    new_pdfs.append({"url": pdf_url, "text": pdf_text})
                    logger.info(f"[{school}] FetchCandidates PDF: {pdf_text[:50]}")
            # ページ内のリンクから入試関連ページを追加（上限を厳しく）
            if len(fetched) < MAX_FETCH:
                sub_links = _extract_links_from_markdown(md, state.domain or "")
                for sub_url in sub_links:
                    if (any(kw in sub_url.lower() for kw in _ADMISSION_KW)
                            and sub_url not in fetched
                            and sub_url not in to_fetch):
                        to_fetch.append(sub_url)
        except Exception as e:
            logger.warning(f"[{school}] FetchCandidates: failed to fetch {url}: {e}")

    logger.info(f"[{school}] FetchCandidates done: {len(new_pdfs)} PDFs collected so far")
    return {"collected_pdfs": new_pdfs, "fetched_urls": fetched}


def node_agent(state: ReactState) -> dict:
    """LLM が不足分を補完するノード（fetch_candidates 後の補完フェーズ）"""
    if state.step_count >= MAX_STEPS:
        logger.warning(f"[{state.school_name}] Max steps reached ({MAX_STEPS}), forcing done")
        return {"is_done": True, "step_count": state.step_count + 1}

    llm = _make_llm().bind_tools(AGENT_TOOLS)

    messages = list(state.messages)
    if not messages:
        pdf_summary = "\n".join(
            f"  - {p['text'][:40]}: {p['url']}"
            for p in state.collected_pdfs[:20]
        ) or "  （なし）"
        messages = [
            SystemMessage(content=_make_system_prompt(state.school_name)),
            HumanMessage(content=(
                f"「{state.school_name}」の確定的スキャンで以下の PDF が収集済みです:\n{pdf_summary}\n\n"
                f"学部・大学院の募集要項が揃っているか確認し、不足があれば search_web で補完してください。"
                f"揃っていれば report_done を呼んでください。"
            )),
        ]

    response: AIMessage = llm.invoke(messages)
    logger.info(f"[{state.school_name}] Step {state.step_count + 1}: {str(response.content)[:100]}")

    is_done = False
    tool_calls = getattr(response, "tool_calls", None) or []
    for tc in tool_calls:
        if tc.get("name") == "report_done":
            is_done = True

    # ツール呼び出しなし → ナッジして再試行（1回のみ）
    if not tool_calls and not is_done and state.step_count < MAX_STEPS - 2:
        nudge = HumanMessage(content=(
            "ツールを呼び出してください。"
            "候補リストの中から次に確認すべき URL を fetch_page するか、"
            "収集完了なら report_done を呼んでください。"
        ))
        response = llm.invoke(messages + [response, nudge])
        tool_calls = getattr(response, "tool_calls", None) or []
        for tc in tool_calls:
            if tc.get("name") == "report_done":
                is_done = True
        logger.info(f"[{state.school_name}] Step {state.step_count + 1} (nudged): tool_calls={len(tool_calls)}")

    return {
        "messages": [response],
        "step_count": state.step_count + 1,
        "is_done": is_done,
    }


def _is_pdf_relevant_extended(text: str, url: str = "") -> bool:
    """
    3段階フィルタ:
    1. 除外キーワードに引っかかる → False
    2. テキスト or URL が正向キーワードに合致 → True
    3. どちらも合致しない → False（ノイズとして除外）
    """
    # 段階1: 除外チェック
    for pat in _PDF_EXCLUDE_EXTRA:
        if pat in text:
            return False
    if not _is_relevant_pdf(text):
        return False

    # 段階2: 正向チェック（テキスト）
    for kw in _PDF_POSITIVE_KW:
        if kw in text:
            return True

    # 段階2: 正向チェック（URL）
    url_lower = url.lower()
    for kw in _PDF_URL_POSITIVE:
        if kw in url_lower:
            return True

    return False


def node_collect_pdfs_from_tool_results(state: ReactState) -> dict:
    """tools_node 実行後、fetch_page の結果から PDF を蓄積する"""
    new_pdfs = list(state.collected_pdfs)
    seen = {p["url"] for p in new_pdfs}

    for msg in reversed(state.messages):
        if not isinstance(msg, ToolMessage):
            break
        tool_name = getattr(msg, "name", "") or ""
        if "fetch_page" not in tool_name:
            continue
        content = msg.content or ""
        for line in content.splitlines():
            m = re.match(r'\s*PDF:\s*(.+?)\s*→\s*(https?://\S+\.pdf\b)', line, re.IGNORECASE)
            if m:
                text, url = m.group(1).strip(), m.group(2).strip()
                if url not in seen and _is_pdf_relevant_extended(text, url):
                    seen.add(url)
                    new_pdfs.append({"url": url, "text": text})
                    logger.info(f"[{state.school_name}] Collected PDF: {text[:50]}")

    return {"collected_pdfs": new_pdfs}


def node_finalize(state: ReactState) -> dict:
    logger.info(f"[{state.school_name}] Finalized. Total PDFs: {len(state.collected_pdfs)}")
    return {}


def _should_continue(state: ReactState):
    if state.is_done:
        return "finalize"
    last = state.messages[-1] if state.messages else None
    if last and isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return "finalize"


# ── グラフ組み立て ────────────────────────────────────────────────────

def build_react_graph():
    tools_node = ToolNode(AGENT_TOOLS)

    builder = StateGraph(ReactState)
    builder.add_node("prepare", node_prepare)
    builder.add_node("fetch_candidates", node_fetch_candidates)
    builder.add_node("agent", node_agent)
    builder.add_node("tools", tools_node)
    builder.add_node("collect_pdfs", node_collect_pdfs_from_tool_results)
    builder.add_node("finalize", node_finalize)

    builder.set_entry_point("prepare")
    builder.add_edge("prepare", "fetch_candidates")
    builder.add_edge("fetch_candidates", "agent")
    builder.add_conditional_edges("agent", _should_continue, {
        "tools": "tools",
        "finalize": "finalize",
    })
    builder.add_edge("tools", "collect_pdfs")
    builder.add_edge("collect_pdfs", "agent")
    builder.add_edge("finalize", END)

    return builder.compile()



