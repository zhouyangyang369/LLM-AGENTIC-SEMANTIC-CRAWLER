"""
ReAct Agent ツール定義

エージェントが自律的に呼び出せるツール群。
PDF 収集の中核ロジックはここに集約。
"""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from langchain_core.tools import tool

from tools.fetcher import fetch_page_as_markdown, extract_pdf_links_from_markdown
from config import PDF_EXCLUDE_TEXT_PATTERNS

logger = logging.getLogger(__name__)


def _is_relevant_pdf(text: str) -> bool:
    for pat in PDF_EXCLUDE_TEXT_PATTERNS:
        if pat in text:
            return False
    return True


@tool
def fetch_page(url: str) -> str:
    """
    指定した URL のページを取得し、以下の情報を返す:
    - このページに直接リンクされた .pdf ファイルの一覧（URL + テキスト）
    - 入試・募集関連と思われるナビゲーションリンクの一覧（URL + テキスト）
    - ページの簡単な要約（最初の 500 文字）

    使用タイミング: 入試情報ページや募集要項が掲載されていそうなページを調べるとき。
    """
    markdown = fetch_page_as_markdown(url)
    if not markdown:
        return f"[ERROR] ページを取得できませんでした: {url}"

    # PDF リンクを直接抽出
    all_pdfs = extract_pdf_links_from_markdown(markdown)
    relevant_pdfs = [p for p in all_pdfs if _is_relevant_pdf(p["text"])]

    # ページ内リンクを抽出（簡易）
    import re
    links = re.findall(r'\[([^\]]+)\]\((https?://[^\)]+)\)', markdown)
    nav_links = [
        {"text": t.strip(), "url": u.strip()}
        for t, u in links
        if not u.lower().endswith(".pdf")
        and len(t.strip()) > 2
    ]

    lines = [f"=== ページ取得結果: {url} ==="]

    if relevant_pdfs:
        lines.append(f"\n【直接リンクされている PDF ({len(relevant_pdfs)}件)】")
        for p in relevant_pdfs:
            lines.append(f"  PDF: {p['text']} → {p['url']}")
    else:
        lines.append("\n【PDF】このページには直接 PDF リンクはありませんでした")

    if nav_links:
        lines.append(f"\n【ナビゲーションリンク（上位20件）】")
        for lnk in nav_links[:20]:
            lines.append(f"  LINK: {lnk['text']} → {lnk['url']}")

    lines.append(f"\n【ページ先頭テキスト】\n{markdown[:600]}")

    return "\n".join(lines)


@tool
def search_web(query: str) -> str:
    """
    Web 検索（Tavily）で入試・募集要項ページを探す。
    サイトマップにない情報や、特定研究科の PDF を探すときに使用する。

    使用タイミング: fetch_page で見つからないとき、特定の研究科の情報を補完するとき。
    引数 query の例: "室蘭工業大学 大学院 募集要項 2025"
    """
    from tools.tavily_search import tavily_search
    try:
        raw = tavily_search(query)
    except Exception:
        raw = []

    if not raw:
        return f"[検索結果なし] クエリ: {query}"

    lines = [f"=== 検索結果: {query} ==="]
    for r in raw[:5]:
        lines.append(f"\nURL: {r.get('url', '')}")
        lines.append(f"タイトル: {r.get('title', '')}")
        lines.append(f"要約: {r.get('content', '')[:200]}")
    return "\n".join(lines)


@tool
def report_done(summary: str) -> str:
    """
    収集作業が完了したことを報告する。
    十分な募集要項 PDF を収集できたと判断したとき、または
    これ以上新しい情報が見つからないと判断したときに呼び出す。

    引数 summary: 収集した内容の簡単なまとめ（何件収集、どんな種類など）
    """
    return f"DONE: {summary}"


# エージェントに渡すツールリスト
AGENT_TOOLS = [fetch_page, search_web, report_done]
