"""
第三阶段爬取 LangGraph 编排图
Ground Truth 驱动爬取流程：
  1. load_targets    : 从 university_units 加载未覆盖目标
  2. search_pdfs     : Tavily 搜索该大学的募集要項 PDF URL
  3. download_pdf    : 下载 PDF 字节流
  4. extract_units   : LLM 结构化提取
  5. match_and_save  : 与 ground truth 对齐，写库
  6. report          : 输出覆盖率报告

节点间通过 GraphState TypedDict 传递状态。
"""
from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import urljoin
from typing_extensions import TypedDict

import httpx
from bs4 import BeautifulSoup
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from tavily import TavilyClient

from src.db.operations import (
    get_uncovered_universities,
    get_units_for_university,
    upsert_crawled_pdf,
    upsert_coverage,
    get_coverage_stats,
)
from src.pipeline.phase3.pdf_downloader import download_pdf, is_pdf_url, PDFDownloadError
from src.pipeline.phase3.pdf_extractor import (
    extract_text_from_bytes,
    detect_pdf_scope,
    build_extraction_prompt,
    parse_llm_extraction_result,
)
from src.pipeline.phase3.unit_matcher import match_units

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# State 定义
# ─────────────────────────────────────────────

class CrawlState(TypedDict):
    """LangGraph 节点间共享的状态"""
    # 任务输入
    university_name: str
    target_year: str

    # ground truth
    known_units: list[dict]

    # 搜索结果
    candidate_urls: list[str]

    # 当前处理的 PDF
    current_url: str
    current_raw_bytes: Optional[bytes]
    current_pdf_scope: Optional[str]
    current_extracted: Optional[dict]   # LLM 提取结果
    current_pdf_id: Optional[str]

    # 累积结果
    processed_urls: list[str]
    failed_urls: list[str]
    coverage_results: list[dict]        # [{unit_id, match_confidence, match_method}]

    # 流程控制
    retry_count: int
    error_message: Optional[str]
    should_continue: bool               # 是否还有更多 URL 需要处理


# ─────────────────────────────────────────────
# 节点函数
# ─────────────────────────────────────────────

def node_load_known_units(state: CrawlState) -> dict:
    """
    节点1: 加载 ground truth
    从 university_units 表读取该大学的已知学部/研究科结构
    """
    university_name = state["university_name"]
    logger.info("[%s] 加载 ground truth 单元...", university_name)

    known_units = get_units_for_university(university_name)
    logger.info("[%s] 找到 %d 个已知 unit", university_name, len(known_units))

    return {
        "known_units": known_units,
        "candidate_urls": [],
        "processed_urls": [],
        "failed_urls": [],
        "coverage_results": [],
        "retry_count": 0,
        "error_message": None,
        "should_continue": True,
        "current_url": "",
        "current_raw_bytes": None,
        "current_pdf_scope": None,
        "current_extracted": None,
        "current_pdf_id": None,
    }


def _discover_pdf_links(page_url: str, timeout: float = 15.0, max_links: int = 10) -> list[str]:
    """
    对非 PDF 搜索结果页面做轻量解析，提取页面中的 PDF 链接。
    Tavily 返回的结果经常是招生页面而非 PDF 直链，这一步能显著提高命中率。
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(
                page_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept-Language": "ja,en;q=0.9",
                },
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            if "pdf" in content_type or resp.content.startswith(b"%PDF"):
                return [str(resp.url)]
            if "html" not in content_type and "text" not in content_type:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            links: list[str] = []
            seen: set[str] = set()
            for a_tag in soup.find_all("a", href=True):
                href = a_tag.get("href") or ""
                text = a_tag.get_text(" ", strip=True)
                absolute = urljoin(str(resp.url), href)
                marker = f"{href} {text}".lower()
                if ("pdf" in marker or absolute.lower().endswith(".pdf")) and absolute not in seen:
                    links.append(absolute)
                    seen.add(absolute)
                if len(links) >= max_links:
                    break
            return links
    except Exception as e:
        logger.debug("PDF 链接发现失败: %s — %s", page_url, e)
        return []


def node_search_pdfs(state: CrawlState) -> dict:
    """
    节点2: Tavily 搜索募集要項 PDF URL
    构造针对性搜索查询，返回候选 PDF URL 列表
    """
    university_name = state["university_name"]
    target_year = state["target_year"]
    logger.info("[%s] 搜索 PDF...", university_name)

    tavily_api_key = os.environ.get("TAVILY_API_KEY")
    if not tavily_api_key:
        logger.error("[%s] 环境变量 TAVILY_API_KEY 未设置，无法搜索 PDF", university_name)
        return {"candidate_urls": [], "should_continue": False}

    tavily = TavilyClient(api_key=tavily_api_key)

    # 构造多个查询，覆盖学部和大学院
    queries = [
        f"{university_name} {target_year} 学生募集要項 filetype:pdf",
        f"{university_name} {target_year} 入試要項 募集要項 PDF",
        f"{university_name} {target_year} 大学院 学生募集要項 filetype:pdf",
    ]

    candidate_urls: list[str] = []
    seen_urls: set[str] = set(state.get("processed_urls", []))

    for query in queries:
        try:
            results = tavily.search(
                query=query,
                search_depth="advanced",
                max_results=5,
                include_domains=[],    # 不限域名
                exclude_domains=[],
            )
            for r in results.get("results", []):
                url = r.get("url", "")
                if not url or url in seen_urls:
                    continue

                if is_pdf_url(url):
                    candidate_urls.insert(0, url)  # PDF URL 优先
                    seen_urls.add(url)
                    continue

                discovered = _discover_pdf_links(url)
                if discovered:
                    for pdf_url in discovered:
                        if pdf_url not in seen_urls:
                            candidate_urls.insert(0, pdf_url)
                            seen_urls.add(pdf_url)
                else:
                    # 保留原始页面作为兜底，download_pdf 会用 content-type/magic bytes 判断
                    candidate_urls.append(url)
                    seen_urls.add(url)
        except Exception as e:
            logger.warning("[%s] Tavily 搜索失败: %s", university_name, e)

    # 去重，保留顺序
    unique_urls: list[str] = []
    seen2: set[str] = set()
    for url in candidate_urls:
        if url not in seen2:
            unique_urls.append(url)
            seen2.add(url)

    logger.info("[%s] 找到 %d 个候选 URL", university_name, len(unique_urls))
    return {"candidate_urls": unique_urls}


def node_pick_next_url(state: CrawlState) -> dict:
    """
    节点3: 从候选列表中选取下一个待处理 URL
    """
    candidate_urls = list(state.get("candidate_urls", []))
    processed_urls = set(state.get("processed_urls", []))
    failed_urls = set(state.get("failed_urls", []))

    # 过滤掉已处理和已失败的
    remaining = [u for u in candidate_urls if u not in processed_urls and u not in failed_urls]

    if not remaining:
        logger.info("[%s] 没有更多候选 URL，结束", state["university_name"])
        return {"should_continue": False, "current_url": ""}

    next_url = remaining[0]
    logger.info("[%s] 选取 URL: %s", state["university_name"], next_url)
    return {"current_url": next_url, "should_continue": True}


def node_download_pdf(state: CrawlState) -> dict:
    """
    节点4: 下载当前 URL 的 PDF
    """
    url = state["current_url"]
    university_name = state["university_name"]

    if not url:
        return {"current_raw_bytes": None, "error_message": "URL 为空"}

    try:
        raw_bytes = download_pdf(url)
        return {
            "current_raw_bytes": raw_bytes,
            "error_message": None,
        }
    except PDFDownloadError as e:
        logger.warning("[%s] 下载失败: %s — %s", university_name, url, e)
        failed = list(state.get("failed_urls", []))
        failed.append(url)
        return {
            "current_raw_bytes": None,
            "error_message": str(e),
            "failed_urls": failed,
        }


def node_extract_units(state: CrawlState) -> dict:
    """
    节点5: LLM 结构化提取 PDF 中的学部/研究科信息
    """
    raw_bytes = state.get("current_raw_bytes")
    university_name = state["university_name"]
    url = state["current_url"]

    if not raw_bytes:
        return {"current_extracted": None, "current_pdf_scope": None}

    # 提取文本
    pdf_text = extract_text_from_bytes(raw_bytes)
    if not pdf_text.strip():
        logger.warning("[%s] PDF 文本为空（可能是扫描件）: %s", university_name, url)
        failed = list(state.get("failed_urls", []))
        failed.append(url)
        return {
            "current_extracted": None,
            "current_pdf_scope": None,
            "failed_urls": failed,
        }

    # 检测 scope
    pdf_scope = detect_pdf_scope(pdf_text)

    # 构造 LLM 提取 prompt
    prompt = build_extraction_prompt(
        university_name=university_name,
        pdf_text=pdf_text,
        known_units=state.get("known_units", []),
    )

    # 调用 LLM
    llm = ChatOpenAI(
        model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        temperature=0,
    )
    try:
        response = llm.invoke(prompt)
        extracted = parse_llm_extraction_result(response.content)
        if extracted:
            logger.info("[%s] LLM 提取成功，covered_units=%d",
                        university_name, len(extracted.get("covered_units", [])))
        else:
            logger.warning("[%s] LLM 返回无法解析", university_name)

        # 补充 scope（以文本检测为准，LLM 结果为参考）
        if extracted:
            extracted["pdf_scope"] = extracted.get("pdf_scope") or pdf_scope

        return {
            "current_extracted": extracted,
            "current_pdf_scope": pdf_scope,
        }
    except Exception as e:
        logger.error("[%s] LLM 调用失败: %s", university_name, e)
        return {
            "current_extracted": None,
            "current_pdf_scope": pdf_scope,
            "error_message": str(e),
        }


def node_match_and_save(state: CrawlState) -> dict:
    """
    节点6: 将 LLM 提取结果与 ground truth 对齐，写入数据库
    """
    university_name = state["university_name"]
    url = state["current_url"]
    raw_bytes = state.get("current_raw_bytes")
    extracted = state.get("current_extracted")
    target_year = state["target_year"]

    if not raw_bytes or not extracted:
        logger.info("[%s] 跳过写库（无有效内容）: %s", university_name, url)
        processed = list(state.get("processed_urls", []))
        processed.append(url)
        return {"processed_urls": processed}

    # ── 写入 crawled_pdfs ──────────────────────────────
    pdf_scope = extracted.get("pdf_scope") or state.get("current_pdf_scope")
    db_result = upsert_crawled_pdf(
        university_name=university_name,
        pdf_url=url,
        raw_bytes=raw_bytes,
        pdf_scope=pdf_scope,
        academic_year=target_year,
        extracted_units=extracted,
    )
    pdf_id = db_result["id"]

    # ── 与 ground truth 匹配 ──────────────────────────
    known_units = state.get("known_units", [])
    covered_units = extracted.get("covered_units", [])

    match_results = match_units(covered_units, known_units)

    # ── 写入 pdf_unit_coverage ─────────────────────────
    coverage_results = list(state.get("coverage_results", []))
    for match in match_results:
        try:
            cov_result = upsert_coverage(
                pdf_id=pdf_id,
                unit_id=match["unit_id"],
                match_confidence=match["confidence"],
                match_method=match["method"],
                target_year=target_year,
            )
            coverage_results.append({
                "unit_id": match["unit_id"],
                "unit_name": match["unit_name"],
                "match_confidence": match["confidence"],
                "match_method": match["method"],
            })
            logger.debug("[%s] 覆盖: %s (%s/%s)",
                         university_name, match["unit_name"],
                         match["confidence"], match["method"])
        except Exception as e:
            logger.error("[%s] 写入覆盖关系失败: %s", university_name, e)

    logger.info("[%s] 写库完成: %s | 匹配 %d/%d unit",
                university_name, url, len(match_results), len(known_units))

    processed = list(state.get("processed_urls", []))
    processed.append(url)

    return {
        "processed_urls": processed,
        "coverage_results": coverage_results,
        "current_pdf_id": pdf_id,
    }


def node_check_coverage(state: CrawlState) -> dict:
    """
    节点7: 检查是否已达到满意覆盖率，决定是否继续处理下一个 URL
    """
    university_name = state["university_name"]
    known_units = state.get("known_units", [])
    coverage_results = state.get("coverage_results", [])
    candidate_urls = state.get("candidate_urls", [])
    processed_urls = set(state.get("processed_urls", []))
    failed_urls = set(state.get("failed_urls", []))

    covered_unit_ids = {r["unit_id"] for r in coverage_results}
    all_unit_ids = {u["id"] for u in known_units}

    remaining_url_count = len([
        u for u in candidate_urls
        if u not in processed_urls and u not in failed_urls
    ])

    # 如果所有 unit 都覆盖了，或没有更多 URL，就停止
    if all_unit_ids and all_unit_ids.issubset(covered_unit_ids):
        logger.info("[%s] 全部 unit 已覆盖 ✓，停止搜索", university_name)
        return {"should_continue": False}
    elif remaining_url_count == 0:
        logger.info("[%s] 无更多候选 URL，停止", university_name)
        return {"should_continue": False}
    else:
        covered_pct = len(covered_unit_ids) / len(all_unit_ids) * 100 if all_unit_ids else 0
        logger.info("[%s] 覆盖率 %.0f%%，继续处理下一个 URL...", university_name, covered_pct)
        return {"should_continue": True}


# ─────────────────────────────────────────────
# 条件路由
# ─────────────────────────────────────────────

def route_after_download(state: CrawlState) -> str:
    """下载后：成功则提取，失败则检查是否继续"""
    if state.get("current_raw_bytes"):
        return "extract"
    return "check_coverage"


def route_after_check(state: CrawlState) -> str:
    """检查覆盖率后：继续或结束"""
    if state.get("should_continue", False):
        return "pick_url"
    return END


# ─────────────────────────────────────────────
# 构建 LangGraph 图
# ─────────────────────────────────────────────

def build_crawl_graph() -> StateGraph:
    """
    构建并编译爬取图。

    节点流程:
        load_units → search_pdfs → pick_url → download
          ↓ (成功)                              ↑ (should_continue)
        extract → match_save → check_coverage ─┘
          ↓ (失败)
        check_coverage
    """
    graph = StateGraph(CrawlState)

    # 添加节点
    graph.add_node("load_units", node_load_known_units)
    graph.add_node("search_pdfs", node_search_pdfs)
    graph.add_node("pick_url", node_pick_next_url)
    graph.add_node("download", node_download_pdf)
    graph.add_node("extract", node_extract_units)
    graph.add_node("match_save", node_match_and_save)
    graph.add_node("check_coverage", node_check_coverage)

    # 设置入口
    graph.set_entry_point("load_units")

    # 添加边
    graph.add_edge("load_units", "search_pdfs")
    graph.add_edge("search_pdfs", "pick_url")
    graph.add_edge("pick_url", "download")

    # 条件路由
    graph.add_conditional_edges(
        "download",
        route_after_download,
        {"extract": "extract", "check_coverage": "check_coverage"},
    )
    graph.add_edge("extract", "match_save")
    graph.add_edge("match_save", "check_coverage")
    graph.add_conditional_edges(
        "check_coverage",
        route_after_check,
        {"pick_url": "pick_url", END: END},
    )

    return graph.compile()


# ─────────────────────────────────────────────
# 单所大学爬取入口
# ─────────────────────────────────────────────

def crawl_university(
    university_name: str,
    target_year: str = "令和7年度",
) -> dict:
    """
    爬取单所大学的募集要項 PDF。

    Returns:
        最终 CrawlState
    """
    graph = build_crawl_graph()

    initial_state: CrawlState = {
        "university_name": university_name,
        "target_year": target_year,
        "known_units": [],
        "candidate_urls": [],
        "current_url": "",
        "current_raw_bytes": None,
        "current_pdf_scope": None,
        "current_extracted": None,
        "current_pdf_id": None,
        "processed_urls": [],
        "failed_urls": [],
        "coverage_results": [],
        "retry_count": 0,
        "error_message": None,
        "should_continue": True,
    }

    final_state = graph.invoke(initial_state)
    return final_state