"""
LangGraph ノード定義 — 各処理ステップを関数として実装する。

ノード一覧:
  node_load_sitemap        — サイトマップを取得・展開
  node_filter_urls         — LLM で関連 URL を選別
  node_find_navigation     — LLM でナビゲーションページを特定
  node_discover_subsites   — ナビページからサブサイトを抽出
  node_crawl_pages         — 候補ページを取得・PDF 抽出
  node_process_subsites    — サブサイトのサイトマップを展開・再クロール
  node_tavily_fallback     — Tavily で不足分を補完
  node_audit_completeness  — LLM で完備性を審査
  node_finalize            — 最終結果を整形
"""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agent.schemas import (
    AgentState, SubsiteInfo, PDFEntry,
    FilterResult, NavigationResult, SubsiteResult,
    PageExtractionResult, AuditResult,
)
from config import (
    LLM_FILTER_BATCH, MAX_PAGES_PER_SCHOOL, MAX_AUDIT_ROUNDS,
    ADMISSION_KEYWORDS_JA, PDF_EXCLUDE_TEXT_PATTERNS,
)
from llm.client import llm_call_structured, llm_call_json
from llm.prompts import (
    prompt_filter_sitemap_urls,
    prompt_find_navigation_pages,
    prompt_extract_subsites,
    prompt_extract_pdfs_from_page,
    prompt_audit_completeness,
)
from tools.sitemap_parser import discover_sitemap, fetch_sitemap_urls
from tools.fetcher import fetch_page_as_markdown, extract_pdf_links_from_markdown
from tools.tavily_search import tavily_search_admission

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
#  ユーティリティ
# ────────────────────────────────────────────────────────────────────

def _keyword_prefilter(urls: list[str]) -> list[str]:
    """キーワードマッチで URL を粗く絞る（LLM 呼び出し前の前処理）"""
    keywords_lower = [k.lower() for k in ADMISSION_KEYWORDS_JA]
    result = []
    for url in urls:
        url_lower = url.lower()
        if any(kw in url_lower for kw in keywords_lower):
            result.append(url)
    return result


def _is_relevant_pdf(text: str) -> bool:
    """リンクテキストが除外パターンに一致する場合 False を返す"""
    for pat in PDF_EXCLUDE_TEXT_PATTERNS:
        if pat in text:
            return False
    return True


def _add_pdf(state: AgentState, pdf: PDFEntry, source_page: str = "") -> None:
    # LLM が PDF ではなくページ URL を返す誤りを防ぐ
    base_url = pdf.url.split("?")[0].split("#")[0]
    if not base_url.lower().endswith(".pdf"):
        return
    # 募集要項に無関係な PDF を除外
    if not _is_relevant_pdf(pdf.text):
        logger.debug(f"Excluded non-relevant PDF: {pdf.text} ({pdf.url})")
        return
    if pdf.url not in state.seen_pdf_urls:
        state.seen_pdf_urls.add(pdf.url)
        if source_page:
            pdf.source_page = source_page
        state.pdfs.append(pdf)


def _direct_pdf_extract(state: AgentState, markdown: str, source_url: str) -> int:
    """Markdown から正規表現で .pdf リンクを直接抽出（LLM 不要・確実）。追加件数を返す。"""
    added = 0
    for lnk in extract_pdf_links_from_markdown(markdown):
        if not _is_relevant_pdf(lnk["text"]):
            logger.debug(f"Excluded non-relevant PDF: {lnk['text']}")
            continue
        if lnk["url"] not in state.seen_pdf_urls:
            state.seen_pdf_urls.add(lnk["url"])
            state.pdfs.append(PDFEntry(
                url=lnk["url"],
                text=lnk["text"],
                source_page=source_url,
            ))
            added += 1
    return added


def _log(state: AgentState, node: str, msg: str) -> None:
    entry = {"node": node, "msg": msg}
    state.decision_trace.append(entry)
    logger.info(f"[{state.school_name}] [{node}] {msg}")


# ────────────────────────────────────────────────────────────────────
#  ノード 1: サイトマップ読み込み
# ────────────────────────────────────────────────────────────────────

def node_load_sitemap(state: AgentState) -> AgentState:
    _log(state, "load_sitemap", f"Starting. sitemap_url={state.sitemap_url}")
    urls = discover_sitemap(state.official_url, state.sitemap_url)
    state.all_sitemap_urls = urls
    _log(state, "load_sitemap", f"Found {len(urls)} URLs in sitemap")
    return state


# ────────────────────────────────────────────────────────────────────
#  ノード 2: URL フィルタリング（LLM + キーワード）
# ────────────────────────────────────────────────────────────────────

def node_filter_urls(state: AgentState) -> AgentState:
    all_urls = state.all_sitemap_urls
    _log(state, "filter_urls", f"Total URLs to filter: {len(all_urls)}")

    if not all_urls:
        _log(state, "filter_urls", "No URLs — will rely on Tavily fallback")
        return state

    # キーワード粗フィルタ
    pre_filtered = _keyword_prefilter(all_urls)
    _log(state, "filter_urls", f"Keyword pre-filter: {len(pre_filtered)} / {len(all_urls)}")

    # キーワードヒットが少ない場合は全 URL を LLM に渡す
    pool = pre_filtered if len(pre_filtered) >= 10 else all_urls

    # バッチ分割して LLM に問い合わせ
    candidate_set: set[str] = set()
    for i in range(0, len(pool), LLM_FILTER_BATCH):
        batch = pool[i:i + LLM_FILTER_BATCH]
        prompt = prompt_filter_sitemap_urls(state.school_name, batch)
        result = llm_call_structured(prompt, FilterResult)
        if result:
            candidate_set.update(result.relevant_urls)
            _log(state, "filter_urls", f"Batch {i//LLM_FILTER_BATCH + 1}: LLM selected {len(result.relevant_urls)} URLs")

    state.candidate_pages = list(candidate_set)
    _log(state, "filter_urls", f"Total candidates after LLM filter: {len(state.candidate_pages)}")
    return state


# ────────────────────────────────────────────────────────────────────
#  ノード 3: ナビゲーションページ発見
# ────────────────────────────────────────────────────────────────────

def node_find_navigation(state: AgentState) -> AgentState:
    pool = state.all_sitemap_urls or state.candidate_pages
    if not pool:
        return state

    # 上位 300 URL だけ渡す（長くなりすぎ防止）
    sample = pool[:300]
    prompt = prompt_find_navigation_pages(state.school_name, sample)
    result = llm_call_structured(prompt, NavigationResult)
    if result:
        state.navigation_pages = result.navigation_pages
        _log(state, "find_navigation", f"Found {len(state.navigation_pages)} navigation pages")
    return state


# ────────────────────────────────────────────────────────────────────
#  ノード 4: サブサイト（研究科別サイト）発見
# ────────────────────────────────────────────────────────────────────

def node_discover_subsites(state: AgentState) -> AgentState:
    if not state.navigation_pages:
        return state

    all_subsites: list[SubsiteInfo] = []
    for nav in state.navigation_pages[:5]:  # 最大 5 ページ
        url = nav.get("url", "")
        if not url or url in state.visited_pages:
            continue
        state.visited_pages.add(url)

        markdown = fetch_page_as_markdown(url)
        if not markdown:
            continue

        prompt = prompt_extract_subsites(state.school_name, markdown, url)
        result = llm_call_structured(prompt, SubsiteResult)
        if result:
            all_subsites.extend(result.subsites)
            _log(state, "discover_subsites", f"{url}: found {len(result.subsites)} subsites")

    # 重複排除
    seen_urls: set[str] = set()
    for sub in all_subsites:
        if sub.url not in seen_urls:
            seen_urls.add(sub.url)
            state.discovered_subsites.append(sub)

    _log(state, "discover_subsites", f"Total subsites: {len(state.discovered_subsites)}")
    return state


# ────────────────────────────────────────────────────────────────────
#  ノード 5: 候補ページクロール + PDF 抽出
# ────────────────────────────────────────────────────────────────────

def node_crawl_pages(state: AgentState) -> AgentState:
    queue = list(state.candidate_pages)

    # follow_queue にあるリンクも追加
    queue.extend(state.follow_queue)
    state.follow_queue = []

    _log(state, "crawl_pages", f"Crawling {len(queue)} pages")
    pages_crawled = 0

    for url in queue:
        if url in state.visited_pages:
            continue
        if pages_crawled >= MAX_PAGES_PER_SCHOOL:
            _log(state, "crawl_pages", f"Reached max pages limit ({MAX_PAGES_PER_SCHOOL})")
            break

        state.visited_pages.add(url)
        pages_crawled += 1

        markdown = fetch_page_as_markdown(url)
        if not markdown:
            continue

        # まず正規表現で確実に .pdf リンクを取得（LLM 依存なし）
        direct_count = _direct_pdf_extract(state, markdown, url)
        if direct_count:
            _log(state, "crawl_pages", f"Direct PDF extract: {direct_count} from {url}")

        # LLM はフォローリンクの判断 + メタデータ補完のみ
        prompt = prompt_extract_pdfs_from_page(state.school_name, url, markdown)
        result = llm_call_structured(prompt, PageExtractionResult)
        if result:
            # LLM が見つけた PDF（正規表現で取れなかったものだけ追加）
            for pdf in result.pdfs:
                _add_pdf(state, pdf, source_page=url)
            # フォローリンクをキューに追加
            for link in result.follow_links:
                follow_url = link.get("url", "")
                if follow_url and follow_url not in state.visited_pages:
                    state.follow_queue.append(follow_url)
            depts = list({p.department for p in result.pdfs if p.department})
            state.found_departments.extend(depts)

    state.found_departments = list(set(state.found_departments))
    _log(state, "crawl_pages", f"Crawled {pages_crawled} pages, found {len(state.pdfs)} PDFs so far")
    return state


# ────────────────────────────────────────────────────────────────────
#  ノード 6: サブサイト処理（各研究科サイト）
# ────────────────────────────────────────────────────────────────────

def node_process_subsites(state: AgentState) -> AgentState:
    if not state.discovered_subsites:
        return state

    _log(state, "process_subsites", f"Processing {len(state.discovered_subsites)} subsites")

    for sub in state.discovered_subsites:
        if sub.url in state.visited_pages:
            continue

        _log(state, "process_subsites", f"Processing subsite: {sub.name} → {sub.url}")

        # サブサイトのサイトマップを探索
        sub_urls = discover_sitemap(sub.url)

        if sub_urls:
            # キーワードフィルタ後 LLM 選別
            pre = _keyword_prefilter(sub_urls) or sub_urls[:50]
            prompt = prompt_filter_sitemap_urls(f"{state.school_name} {sub.name}", pre[:LLM_FILTER_BATCH])
            filter_result = llm_call_structured(prompt, FilterResult)
            pages_to_crawl = filter_result.relevant_urls if filter_result else pre[:20]
        else:
            # サイトマップなし → トップページをそのまま
            pages_to_crawl = [sub.url]

        for url in pages_to_crawl:
            if url in state.visited_pages or len(state.visited_pages) > MAX_PAGES_PER_SCHOOL * 2:
                continue
            state.visited_pages.add(url)

            markdown = fetch_page_as_markdown(url)
            if not markdown:
                continue

            prompt = prompt_extract_pdfs_from_page(
                f"{state.school_name} {sub.name}", url, markdown
            )
            result = llm_call_structured(prompt, PageExtractionResult)
            if result:
                for pdf in result.pdfs:
                    if not pdf.department:
                        pdf.department = sub.name
                    _add_pdf(state, pdf, source_page=url)
                if sub.name and any(p.department == sub.name for p in result.pdfs):
                    if sub.name not in state.found_departments:
                        state.found_departments.append(sub.name)

    _log(state, "process_subsites", f"After subsites: {len(state.pdfs)} total PDFs")
    return state


# ────────────────────────────────────────────────────────────────────
#  ノード 7: フォローキュー処理（crawl_pages からの追加リンク）
# ────────────────────────────────────────────────────────────────────

def node_crawl_follow_queue(state: AgentState) -> AgentState:
    if not state.follow_queue:
        return state

    _log(state, "crawl_follow", f"Following {len(state.follow_queue)} links")

    queue = list(state.follow_queue)
    state.follow_queue = []

    for url in queue:
        if url in state.visited_pages or len(state.visited_pages) > MAX_PAGES_PER_SCHOOL * 2:
            continue
        state.visited_pages.add(url)

        markdown = fetch_page_as_markdown(url)
        if not markdown:
            continue

        # 直接抽出
        direct_count = _direct_pdf_extract(state, markdown, url)
        if direct_count:
            _log(state, "crawl_follow", f"Direct PDF extract: {direct_count} from {url}")

        # LLM でメタデータ補完
        prompt = prompt_extract_pdfs_from_page(state.school_name, url, markdown)
        result = llm_call_structured(prompt, PageExtractionResult)
        if result:
            for pdf in result.pdfs:
                _add_pdf(state, pdf, source_page=url)

    _log(state, "crawl_follow", f"After follow: {len(state.pdfs)} total PDFs")
    return state


# ────────────────────────────────────────────────────────────────────
#  ノード 8: Tavily フォールバック
# ────────────────────────────────────────────────────────────────────

def node_tavily_fallback(state: AgentState) -> AgentState:
    """
    サイトマップが空 or 収集が少ない場合に Tavily で補完。
    また missing_departments に対して Tavily を使って再検索する。
    """
    should_run = (
        not state.all_sitemap_urls          # サイトマップなし
        or len(state.pdfs) < 3              # PDF が少なすぎ
        or state.missing_departments        # 未取得研究科あり
    )
    if not should_run:
        return state

    _log(state, "tavily_fallback", "Running Tavily fallback search")

    targets = state.missing_departments if state.missing_departments else [""]

    for dept in targets[:5]:
        results = tavily_search_admission(state.school_name, dept, state.domain)
        for r in results:
            url = r.get("url", "")
            if not url or url in state.visited_pages:
                continue
            state.visited_pages.add(url)

            markdown = fetch_page_as_markdown(url)
            if not markdown:
                continue

            prompt = prompt_extract_pdfs_from_page(state.school_name, url, markdown)
            result = llm_call_structured(prompt, PageExtractionResult)
            if result:
                for pdf in result.pdfs:
                    _add_pdf(state, pdf, source_page=url)

    _log(state, "tavily_fallback", f"After Tavily: {len(state.pdfs)} total PDFs")
    return state


# ────────────────────────────────────────────────────────────────────
#  ノード 9: 完備性審査（LLM が研究科の漏れを自己チェック）
# ────────────────────────────────────────────────────────────────────

def node_audit_completeness(state: AgentState) -> AgentState:
    if state.audit_rounds >= MAX_AUDIT_ROUNDS:
        state.is_complete = True
        return state

    state.audit_rounds += 1
    _log(state, "audit", f"Round {state.audit_rounds}: auditing completeness")

    prompt = prompt_audit_completeness(
        state.school_name,
        state.found_departments,
        [p.model_dump() for p in state.pdfs],
    )
    result = llm_call_structured(prompt, AuditResult)
    if result:
        state.missing_departments = result.missing_departments
        state.is_complete = result.is_complete
        _log(
            state, "audit",
            f"Complete={result.is_complete}, Missing={result.missing_departments}, "
            f"Note={result.completeness_note}"
        )
        # 不足研究科の検索クエリを follow_queue に変換（Tavily ノードで処理）
        for query in result.suggested_queries[:5]:
            state.follow_queue.append(f"__tavily_query__:{query}")
    else:
        state.is_complete = True  # パース失敗時は完了とみなす

    return state


# ────────────────────────────────────────────────────────────────────
#  ノード 10: 最終整形
# ────────────────────────────────────────────────────────────────────

def node_finalize(state: AgentState) -> AgentState:
    # PDF の重複最終チェック
    seen: set[str] = set()
    unique_pdfs: list[PDFEntry] = []
    for pdf in state.pdfs:
        if pdf.url not in seen:
            seen.add(pdf.url)
            unique_pdfs.append(pdf)
    state.pdfs = unique_pdfs
    _log(state, "finalize", f"Final PDF count: {len(state.pdfs)}")
    return state
