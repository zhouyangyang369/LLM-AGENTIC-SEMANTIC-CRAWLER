"""
第三阶段爬取 LangGraph 编排图
Ground Truth 驱动爬取流程：
  1. load_targets    : 从 university_units 加载未覆盖目标
  2. search_pdfs     : 基于 Ground Truth 学部/研究科生成精准查询，Tavily 搜索
  3. filter_url      : 规则 + LLM 过滤无关 URL（借鉴 Phase2）
  4. download_pdf    : 下载 PDF 字节流
  5. extract_units   : LLM 结构化提取
  6. match_and_save  : 与 ground truth 对齐，写库
  7. check_coverage  : 覆盖率判断，决定是否继续

节点间通过 GraphState TypedDict 传递状态。
"""
from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import urljoin
from typing_extensions import TypedDict

import re
import time
import httpx
from bs4 import BeautifulSoup
from langgraph.graph import StateGraph, END
from tavily import TavilyClient

from src.db.operations import (
    get_uncovered_universities,
    get_units_for_university,
    upsert_crawled_pdf,
    upsert_coverage,
    get_coverage_stats,
)

# 复用 agentic_crawler 的 LLM 客户端（支持 Portkey / Ollama 统一切换）
import sys
import os as _os
_agentic_path = _os.path.join(_os.path.dirname(__file__), "..", "..", "..", "agentic_crawler")
sys.path.insert(0, _os.path.abspath(_agentic_path))
from llm.client import llm_call
from config import TAVILY_API_KEY as _TAVILY_API_KEY_DEFAULT
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
# 常量：无关文档的规则过滤关键词（借鉴 Phase2 的排除模式）
# ─────────────────────────────────────────────

# URL 或文件名中出现以下关键词 → 直接排除，不需要 LLM 判断
_URL_EXCLUDE_PATTERNS: list[str] = [
    "syllabus", "shillabus", "シラバス",
    "gakusei_binran", "student_guide", "便覧", "便览",
    "kinenshi", "kiyou", "紀要",
    "houkoku", "nenjihoukoku",
    "zaigaku", "sotsugyou", "nyugakushagaiyou", "nyuugakusyagaiyou",
    "goukakusha", "合格者",
    "kyouin_boshu", "kyoin_boshu", "faculty_recruit", "教員募集",
    "kenkyusha_boshu", "researcher",
    "campus_map", "campusmap",
    "timetable", "jikanwari",
    # 共通テスト・センター試験関連（大学個別の募集要項ではない）
    "kyotsu", "center_test", "daigaku_nyushi_center", "dnc.ac.jp",
    "shiken_kaijo", "試験場",
    # 文科省の全大学一覧・統計（個別大学の募集要項ではない）
    "daigaku_ichiran", "mext_daigakuc",
    # 第三方教育情報プラットフォーム（個別大学公式PDFではない）
    "janu.jp", "keinet.ne.jp", "benesse", "kawaijuku",
    "mynavi", "rikunabi", "passnavi", "keiyu",
    "kobekyo.com", "nyushi.yahoo",
]

# URL 中出现以下关键词 → 正向信号，优先处理
_URL_POSITIVE_PATTERNS: list[str] = [
    "boshu", "youkou", "yoko", "nyushi", "senbatsu",
    "shutsugan", "admission", "recruit", "daigakuin",
    "gakubu", "kenkyuka", "masters", "doctor",
    "ippan", "suisen", "sogouga",
]

# Tavily 搜索间隔（避免频率限制）
_TAVILY_SLEEP: float = 1.2


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
    # ⚠️ 不在 State 中存储原始 bytes（防止大 PDF 导致序列化爆内存）
    # 改为只存提取后的文本，bytes 在节点内部局部变量中处理
    current_pdf_text: Optional[str]     # PDF 提取的纯文本
    current_pdf_scope: Optional[str]
    current_extracted: Optional[dict]   # LLM 提取结果
    current_pdf_id: Optional[str]
    current_raw_bytes: Optional[bytes]  # 仅在 download→match_save 同步路径中使用，不跨多节点

    # 累积结果
    processed_urls: list[str]
    failed_urls: list[str]
    coverage_results: list[dict]        # [{unit_id, match_confidence, match_method}]

    # 流程控制
    retry_count: int
    error_message: Optional[str]
    should_continue: bool               # 是否还有更多 URL 需要处理
    current_url_skipped: bool           # 当前 URL 是否被过滤器跳过


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
        "current_url_skipped": False,
        "current_raw_bytes": None,
        "current_pdf_text": None,
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


def _build_search_queries(university_name: str, target_year: str, known_units: list[dict]) -> list[str]:
    """
    基于 Ground Truth 的学部/研究科名称构造精准搜索查询。

    策略：
    - 将 known_units 按 unit_type（学部/研究科）分组，每组取代表性名称
    - 对学部/研究科分别生成查询，命中率比泛查询高
    - 最多生成 5 条查询，避免 Tavily 调用过多
    """
    queries: list[str] = []

    # 按 unit_type 分组，取不重复的 unit_name
    gakubu_names: list[str] = []
    kenkyuka_names: list[str] = []
    for u in known_units:
        name = u.get("unit_name", "")
        if not name:
            continue
        if u.get("unit_type") == "学部" and name not in gakubu_names:
            gakubu_names.append(name)
        elif u.get("unit_type") == "研究科" and name not in kenkyuka_names:
            kenkyuka_names.append(name)

    # 学部クエリ：最多取前2个学部名，拼接成一条查询
    if gakubu_names:
        name_str = " ".join(gakubu_names[:2])
        queries.append(f"{university_name} {name_str} 学生募集要項 {target_year}")
        queries.append(f"{university_name} {name_str} 入学者選抜要項 filetype:pdf")

    # 研究科クエリ：最多取前2个研究科名
    if kenkyuka_names:
        name_str = " ".join(kenkyuka_names[:2])
        queries.append(f"{university_name} {name_str} 学生募集要項 {target_year}")

    # 兜底：泛查询（保证至少有结果）
    queries.append(f"{university_name} 募集要項 入試 filetype:pdf {target_year}")
    queries.append(f"{university_name} admission PDF {target_year}")

    # 去重保序，最多 5 条
    seen: set[str] = set()
    result: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            result.append(q)
        if len(result) >= 5:
            break
    return result


def node_search_pdfs(state: CrawlState) -> dict:
    """
    节点2: 基于 Ground Truth 生成精准查询，Tavily 搜索募集要項 PDF URL。

    改进（vs 原版）：
    - 用已知学部/研究科名称构造精准查询，而非泛查询
    - 对搜索结果中属于目标大学域名的 URL 优先排列
    - 对非 PDF 页面自动发现其中的 PDF 链接
    """
    university_name = state["university_name"]
    target_year = state["target_year"]
    known_units = state.get("known_units", [])
    logger.info("[%s] 搜索 PDF（Ground Truth 精准模式）...", university_name)

    # 优先读 .env 环境变量，回退到 agentic_crawler/config.py 的默认值
    tavily_api_key = os.environ.get("TAVILY_API_KEY") or _TAVILY_API_KEY_DEFAULT
    if not tavily_api_key:
        logger.error("[%s] TAVILY_API_KEY 未配置，无法搜索 PDF", university_name)
        return {"candidate_urls": [], "should_continue": False}

    tavily = TavilyClient(api_key=tavily_api_key)
    queries = _build_search_queries(university_name, target_year, known_units)
    logger.info("[%s] 生成 %d 条搜索查询: %s", university_name, len(queries), queries)

    # 分离：目标大学域名 URL（优先）vs 其他域名 URL（兜底）
    priority_urls: list[str] = []   # 目标大学域名的 URL
    fallback_urls: list[str] = []   # 其他域名的 URL
    seen_urls: set[str] = set(state.get("processed_urls", []))

    # 推断目标大学的域名关键词（取大学名罗马字简写，Tavily 结果里的 URL 含此词则优先）
    # 这里用简单启发：URL 中不含其他大学名的汉字 → 视为可能相关
    # 更准确的方式：第一条查询结果的域名作为目标域名
    target_domain: str = ""

    for i, query in enumerate(queries):
        try:
            results = tavily.search(
                query=query,
                search_depth="advanced",
                max_results=5,
                include_domains=[],
                exclude_domains=[],
            )
            # 第一条查询结果的第一个 URL 的域名作为目标域名
            if i == 0 and not target_domain:
                for r in results.get("results", []):
                    url = r.get("url", "")
                    if url:
                        from urllib.parse import urlparse
                        target_domain = urlparse(url).netloc
                        logger.info("[%s] 推断目标域名: %s", university_name, target_domain)
                        break

            for r in results.get("results", []):
                url = r.get("url", "")
                if not url or url in seen_urls:
                    continue

                is_target = target_domain and target_domain in url

                if is_pdf_url(url):
                    if is_target:
                        priority_urls.insert(0, url)
                    else:
                        fallback_urls.append(url)
                    seen_urls.add(url)
                    continue

                # 非 PDF 页面：尝试发现其中的 PDF 链接
                discovered = _discover_pdf_links(url)
                if discovered:
                    for pdf_url in discovered:
                        if pdf_url not in seen_urls:
                            if is_target or (target_domain and target_domain in pdf_url):
                                priority_urls.insert(0, pdf_url)
                            else:
                                fallback_urls.append(pdf_url)
                            seen_urls.add(pdf_url)
                else:
                    if is_target:
                        priority_urls.append(url)
                    else:
                        fallback_urls.append(url)
                    seen_urls.add(url)

            if i < len(queries) - 1:
                time.sleep(_TAVILY_SLEEP)

        except Exception as e:
            logger.warning("[%s] Tavily 搜索失败 [query=%s]: %s", university_name, query, e)

    # 合并：目标域名优先，其他兜底
    combined = priority_urls + fallback_urls

    # 最终去重保序
    unique_urls: list[str] = []
    seen2: set[str] = set()
    for url in combined:
        if url not in seen2:
            unique_urls.append(url)
            seen2.add(url)

    logger.info(
        "[%s] 候选 URL: %d 条（目标域名=%d, 其他=%d）",
        university_name, len(unique_urls), len(priority_urls), len(fallback_urls)
    )
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
        return {"should_continue": False, "current_url": "", "current_url_skipped": False}

    next_url = remaining[0]
    logger.info("[%s] 选取 URL: %s", state["university_name"], next_url)
    return {"current_url": next_url, "should_continue": True, "current_url_skipped": False}


# ─────────────────────────────────────────────
# 规则过滤函数（无 LLM，快速）
# ─────────────────────────────────────────────

def _rule_filter_url(url: str) -> tuple[bool, str]:
    """
    基于 URL/文件名关键词快速过滤明显无关的文档。

    Returns:
        (should_skip, reason)  should_skip=True 表示应该跳过
    """
    url_lower = url.lower()
    for pat in _URL_EXCLUDE_PATTERNS:
        if pat.lower() in url_lower:
            return True, f"URL 含排除关键词: {pat}"
    return False, ""


def _llm_filter_url(url: str, university_name: str, known_units: list[dict]) -> tuple[bool, str]:
    """
    用 LLM 判断 URL 是否为目标大学的募集要項相关文档。
    只对规则无法判断的 URL 调用（节省 token）。

    Returns:
        (should_skip, reason)  should_skip=True 表示应该跳过
    """
    # 构造已知学部/研究科摘要，辅助 LLM 判断
    units_summary = ""
    if known_units:
        names = list({u.get("unit_name", "") for u in known_units if u.get("unit_name")})
        units_summary = "、".join(names[:8])

    prompt = f"""以下のURLが「{university_name}」の入学者選抜・学生募集要項に関連するPDF/ページかどうか判定してください。

【大学名】{university_name}
【この大学の学部・研究科】{units_summary}
【判定対象URL】{url}

【判定基準】
- 関連する（KEEP）: 募集要項、入試要項、選抜要項、出願要領、入学案内PDF、当該大学の入試情報ページ
- 関連しない（SKIP）: 他大学のPDF、シラバス、学生便覧、教員募集、研究報告、合格者発表、紀要、採点基準、第三者情報サイト

【回答形式】必ず以下のJSONのみで回答。説明不要。
{{"verdict": "KEEP" or "SKIP", "reason": "一行で理由"}}"""

    try:
        raw = llm_call(
            prompt=prompt,
            system="あなたはURLの内容を判定する専門家です。必ずJSONのみで回答してください。",
            model_role="primary",   # 判断任务用 primary 模型（更快更便宜）
            temperature=0,
            max_tokens=128,
        )
        # 解析 JSON
        json_match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if json_match:
            import json
            result = json.loads(json_match.group())
            verdict = result.get("verdict", "KEEP").upper()
            reason = result.get("reason", "")
            if verdict == "SKIP":
                return True, f"LLM 判断跳过: {reason}"
            else:
                return False, f"LLM 判断保留: {reason}"
    except Exception as e:
        logger.warning("LLM 过滤调用失败，保守跳过（避免浪费下载带宽）: %s — %s", url, e)
        # LLM 调用失败时：对非目标大学域名 URL 保守跳过；对 .pdf 直链保留
        if url.lower().endswith(".pdf"):
            return False, "LLM失败但为PDF直链，保留"
        return True, "LLM调用失败，非PDF链接保守跳过"

    return False, "LLM 判断失败，默认保留"


def node_filter_url(state: CrawlState) -> dict:
    """
    节点3.5（新增）: 在下载前对当前 URL 做相关性过滤。
    借鉴 Phase2 的相关性判断逻辑，分两级：
      Level 1 - 规则过滤（快速，无 LLM）：URL 含明确无关关键词 → 直接跳过
      Level 2 - LLM 过滤（精准）：URL 含正向信号 or 规则无法判断 → LLM 判断
    """
    url = state.get("current_url", "")
    university_name = state["university_name"]
    known_units = state.get("known_units", [])

    if not url:
        return {"current_url_skipped": True}

    # Level 1: 规则过滤（无 LLM）
    should_skip, reason = _rule_filter_url(url)
    if should_skip:
        logger.info("[%s] 规则过滤跳过: %s — %s", university_name, url[:80], reason)
        failed = list(state.get("failed_urls", []))
        failed.append(url)
        return {"current_url_skipped": True, "failed_urls": failed}

    # Level 2: 检查正向信号 —— 含正向关键词的直接保留，不调用 LLM
    url_lower = url.lower()
    has_positive = any(pat in url_lower for pat in _URL_POSITIVE_PATTERNS)
    if has_positive:
        logger.debug("[%s] 正向信号，直接保留: %s", university_name, url[:80])
        return {"current_url_skipped": False}

    # Level 3: LLM 过滤（对规则无法判断的 URL）
    # 只对非明显 PDF URL 调用 LLM（直接 .pdf 结尾的已经很可能相关）
    if url_lower.endswith(".pdf"):
        logger.debug("[%s] PDF 直链，直接保留: %s", university_name, url[:80])
        return {"current_url_skipped": False}

    logger.debug("[%s] 调用 LLM 过滤判断: %s", university_name, url[:80])
    should_skip, reason = _llm_filter_url(url, university_name, known_units)
    if should_skip:
        logger.info("[%s] LLM 过滤跳过: %s — %s", university_name, url[:80], reason)
        failed = list(state.get("failed_urls", []))
        failed.append(url)
        return {"current_url_skipped": True, "failed_urls": failed}

    logger.debug("[%s] 过滤通过，继续下载: %s", university_name, url[:80])
    return {"current_url_skipped": False}


def node_download_pdf(state: CrawlState) -> dict:
    """
    节点4: 下载当前 URL 的 PDF，并立即提取文本，不在 State 中保留原始 bytes。
    """
    url = state["current_url"]
    university_name = state["university_name"]

    if not url:
        return {
            "current_raw_bytes": None,
            "current_pdf_text": None,
            "error_message": "URL 为空",
        }

    try:
        raw_bytes = download_pdf(url)
        # 立即提取文本，不把大字节流保留在 State 里
        pdf_text = extract_text_from_bytes(raw_bytes)
        logger.info("[%s] 下载+提取成功: %s (文字数=%d)", university_name, url, len(pdf_text))
        return {
            "current_raw_bytes": raw_bytes,   # match_save 节点写库时还需要计算 hash
            "current_pdf_text": pdf_text,
            "error_message": None,
        }
    except PDFDownloadError as e:
        logger.warning("[%s] 下载失败: %s — %s", university_name, url, e)
        failed = list(state.get("failed_urls", []))
        failed.append(url)
        return {
            "current_raw_bytes": None,
            "current_pdf_text": None,
            "error_message": str(e),
            "failed_urls": failed,
        }


def node_extract_units(state: CrawlState) -> dict:
    """
    节点5: LLM 结构化提取 PDF 中的学部/研究科信息。
    使用 agentic_crawler/llm/client.py 的 llm_call，支持 Portkey / Ollama 统一切换。
    文本直接从 State 的 current_pdf_text 字段读取（download 节点已提取）。
    """
    pdf_text = state.get("current_pdf_text") or ""
    university_name = state["university_name"]
    url = state["current_url"]

    if not pdf_text.strip():
        logger.warning("[%s] PDF 文本为空（可能是扫描件或下载失败）: %s", university_name, url)
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

    # 调用 LLM（复用 agentic_crawler 的统一客户端，自动走 Portkey / Ollama）
    try:
        system_prompt = (
            "あなたは大学の募集要項PDFから情報を抽出する専門家です。"
            "必ず指定されたJSON形式のみで回答してください。説明文は不要です。"
        )
        raw_response = llm_call(
            prompt=prompt,
            system=system_prompt,
            model_role="extract",   # 使用 PORTKEY_EXTRACT_MODEL（Claude）做结构化提取
            temperature=0,
            max_tokens=4096,
        )
        extracted = parse_llm_extraction_result(raw_response)
        if extracted:
            logger.info(
                "[%s] LLM 提取成功，covered_units=%d",
                university_name, len(extracted.get("covered_units", []))
            )
        else:
            logger.warning("[%s] LLM 返回无法解析，原文前200字: %s", university_name, raw_response[:200])

        # 补充 scope（以文本关键词检测为准，LLM 结果为参考）
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

    if not extracted:
        logger.info("[%s] 跳过写库（LLM 未提取到有效内容）: %s", university_name, url)
        processed = list(state.get("processed_urls", []))
        processed.append(url)
        return {"processed_urls": processed, "current_raw_bytes": None}

    if not raw_bytes:
        logger.warning("[%s] 跳过写库（raw_bytes 丢失，无法计算 hash）: %s", university_name, url)
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
        "current_raw_bytes": None,   # 写库完成后立即清空，释放内存
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

def route_after_filter(state: CrawlState) -> str:
    """过滤后：跳过则回 check_coverage，通过则继续下载"""
    if state.get("current_url_skipped", False):
        return "check_coverage"
    return "download"


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

    节点流程（改进版）:
        load_units → search_pdfs → pick_url → filter_url
                                       ↑            ↓ 通过          ↓ 跳过
                                       │          download      check_coverage
                                       │       ↓ 成功  ↓ 失败        │
                                       │     extract  check_coverage  │
                                       │       ↓                      │
                                       │    match_save                │
                                       │       ↓                      │
                                       └── check_coverage ────────────┘
    """
    graph = StateGraph(CrawlState)

    # 添加节点
    graph.add_node("load_units",    node_load_known_units)
    graph.add_node("search_pdfs",   node_search_pdfs)
    graph.add_node("pick_url",      node_pick_next_url)
    graph.add_node("filter_url",    node_filter_url)       # 新增：相关性过滤
    graph.add_node("download",      node_download_pdf)
    graph.add_node("extract",       node_extract_units)
    graph.add_node("match_save",    node_match_and_save)
    graph.add_node("check_coverage",node_check_coverage)

    # 设置入口
    graph.set_entry_point("load_units")

    # 固定边
    graph.add_edge("load_units",  "search_pdfs")
    graph.add_edge("search_pdfs", "pick_url")
    graph.add_edge("pick_url",    "filter_url")  # pick 后先过滤
    graph.add_edge("extract",     "match_save")
    graph.add_edge("match_save",  "check_coverage")

    # 条件路由
    graph.add_conditional_edges(
        "filter_url",
        route_after_filter,
        {"download": "download", "check_coverage": "check_coverage"},
    )
    graph.add_conditional_edges(
        "download",
        route_after_download,
        {"extract": "extract", "check_coverage": "check_coverage"},
    )
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
        "current_url_skipped": False,
        "current_raw_bytes": None,
        "current_pdf_text": None,
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