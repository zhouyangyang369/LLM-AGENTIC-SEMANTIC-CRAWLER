"""
日本国立大学 & 三大先端科技院大 —— 募集要项 PDF 批量抓取脚本

功能：
  1. 遍历预设的 33 所大学（Top 30 国立 + NAIST/JAIST/OIST）
  2. 自动推断官网域名 → Tavily 搜索入试汇总页 → 递归爬取 → 提取 PDF
  3. 每所学校独立输出 JSON（results/<学校名>.json），避免一次性丢失
  4. 失败重试 + 进度条 + API 速率控制

依赖：
  pip install requests beautifulsoup4 tavily-python tqdm
"""

import sys
import io
import os
import json
import time
import random
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm
from tavily import TavilyClient

# Windows 控制台 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ─────────────────────────────────────────
#  配置区
# ─────────────────────────────────────────
TAVILY_API_KEY = "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"

LIMIT = None               # 跑前 N 所学校；None 表示跑全部
CRAWL_DEPTH = 1            # 0=只爬汇总页；1=再跟进一层子页面
MAX_SUBPAGES = 10          # 每个汇总页最多跟进子页面数
REQUEST_TIMEOUT = 15       # HTTP 请求超时（秒）
MAX_RETRY = 3              # Tavily 搜索失败重试次数
RETRY_BACKOFF = 2.0        # 重试退避基数（秒）
TAVILY_SLEEP = 1.0         # 每次 Tavily 调用后的间隔（秒），防限流
SCHOOL_SLEEP = 2.0         # 每所学校之间的间隔（秒）

OUTPUT_DIR = Path("results")
OUTPUT_DIR.mkdir(exist_ok=True)

# 33 所目标大学（Top 30 国立 + 三所先端科技院大）
SCHOOLS = [
    # 第一梯队（旧帝大+东工+一桥）
    "東京大学", "京都大学", "東京科学大学", "大阪大学",
    "東北大学", "名古屋大学", "一橋大学",
    # 第二梯队
    "九州大学", "北海道大学", "筑波大学", "神戸大学",
    # 第三梯队
    "千葉大学", "広島大学", "横浜国立大学", "岡山大学",
    "金沢大学", "東京外国語大学", "東京農工大学",
    "お茶の水女子大学", "東京藝術大学",
    # 第四梯队
    "熊本大学", "新潟大学", "長崎大学", "信州大学",
    "静岡大学", "三重大学", "鹿児島大学", "山口大学",
    "埼玉大学", "岐阜大学",
    # 三大先端科技院大
    "奈良先端科学技術大学院大学",
    "北陸先端科学技術大学院大学",
    "沖縄科学技術大学院大学",
]

client = TavilyClient(TAVILY_API_KEY)

# ─────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────

def get_domain(url: str) -> str:
    """从 URL 提取主域名，例如 https://www.naist.jp/... → naist.jp"""
    parsed = urlparse(url)
    parts = parsed.netloc.split(".")
    if len(parts) >= 3 and parts[-2] in ("ac", "co", "or", "go", "ne"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def fetch_soup(url: str) -> BeautifulSoup | None:
    """请求页面并返回 BeautifulSoup，失败返回 None"""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.encoding = resp.apparent_encoding
        return BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return None


def extract_pdf_links(page_url: str) -> list[dict]:
    """从指定页面提取所有 PDF 链接"""
    soup = fetch_soup(page_url)
    if not soup:
        return []
    pdf_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().split("?")[0].endswith(".pdf"):
            pdf_links.append({
                "text": a.get_text(strip=True),
                "url": urljoin(page_url, href),
                "source_page": page_url,
            })
    return pdf_links


def extract_subpage_links(page_url: str, official_domain: str) -> list[str]:
    """从页面提取同域名下的子页面链接（排除 PDF/图片等文件）"""
    soup = fetch_soup(page_url)
    if not soup:
        return []
    seen, result = set(), []
    bad_ext = (".pdf", ".jpg", ".jpeg", ".png", ".gif",
               ".zip", ".xlsx", ".xls", ".docx", ".doc", ".pptx")
    for a in soup.find_all("a", href=True):
        full_url = urljoin(page_url, a["href"]).split("#")[0]
        parsed = urlparse(full_url)
        if (parsed.scheme in ("http", "https")
                and official_domain in parsed.netloc
                and not any(parsed.path.lower().endswith(ext) for ext in bad_ext)
                and full_url != page_url
                and full_url not in seen):
            seen.add(full_url)
            result.append(full_url)
    return result


def tavily_search_with_retry(query: str, **kwargs) -> dict | None:
    """带重试的 Tavily 搜索"""
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = client.search(query=query, **kwargs)
            time.sleep(TAVILY_SLEEP)
            return resp
        except Exception as e:
            wait = RETRY_BACKOFF ** attempt + random.uniform(0, 1)
            tqdm.write(f"    [Tavily 失败 ({attempt}/{MAX_RETRY}): {e}，{wait:.1f}s 后重试]")
            time.sleep(wait)
    return None


def detect_official_domain(school: str) -> str | None:
    """Step 0: 推断官网域名"""
    resp = tavily_search_with_retry(
        f"{school} 公式サイト",
        search_depth="basic",
        max_results=3,
    )
    if not resp:
        return None
    for r in resp.get("results", []):
        domain = get_domain(r["url"])
        if "ac.jp" in domain or "oist.jp" in domain:
            return domain
    # 兜底：返回第一个结果的域名
    if resp.get("results"):
        return get_domain(resp["results"][0]["url"])
    return None


def collect_candidate_pages(school: str, official_domain: str | None) -> list[str]:
    """Step 1: 用泛化 Query 收集入试汇总页"""
    site_filter = f" site:{official_domain}" if official_domain else ""
    queries = [
        f"{school} 募集要項{site_filter}",
        f"{school} 大学院 入試情報{site_filter}",
        f"{school} 入学者選抜要項{site_filter}",
        f"{school} 入試 一覧{site_filter}",
    ]
    seen, urls = set(), []
    for q in queries:
        resp = tavily_search_with_retry(q, search_depth="advanced", max_results=5)
        if not resp:
            continue
        for r in resp.get("results", []):
            url = r["url"]
            if official_domain and official_domain not in url:
                continue
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def crawl_pdfs(candidate_urls: list[str], official_domain: str | None) -> list[dict]:
    """Step 2: 爬取页面 + 递归子页面，提取 PDF"""
    all_pdfs: list[dict] = []
    seen_pdf: set[str] = set()
    visited: set[str] = set()

    def _crawl(url: str, depth: int):
        if url in visited:
            return
        visited.add(url)
        for pdf in extract_pdf_links(url):
            if pdf["url"] not in seen_pdf:
                seen_pdf.add(pdf["url"])
                all_pdfs.append(pdf)
        if depth > 0 and official_domain:
            for sub_url in extract_subpage_links(url, official_domain)[:MAX_SUBPAGES]:
                _crawl(sub_url, depth - 1)

    for url in candidate_urls:
        _crawl(url, CRAWL_DEPTH)
    return all_pdfs


def safe_filename(name: str) -> str:
    """生成安全的文件名"""
    bad = '<>:"/\\|?*'
    for ch in bad:
        name = name.replace(ch, "_")
    return name


# ─────────────────────────────────────────
#  主流程
# ─────────────────────────────────────────

def process_school(school: str) -> dict:
    """处理单所学校，返回结果字典"""
    result = {
        "school": school,
        "official_domain": None,
        "candidate_pages": [],
        "pdfs": [],
        "error": None,
    }
    try:
        domain = detect_official_domain(school)
        result["official_domain"] = domain

        candidates = collect_candidate_pages(school, domain)
        result["candidate_pages"] = candidates

        pdfs = crawl_pdfs(candidates, domain)
        result["pdfs"] = pdfs
    except Exception as e:
        result["error"] = str(e)
    return result


def main():
    print("=" * 70)
    print(f"  日本国立大学 募集要项 PDF 批量抓取（共 {len(SCHOOLS)} 所）")
    print(f"  输出目录: {OUTPUT_DIR.resolve()}")
    print("=" * 70)

    targets = SCHOOLS if LIMIT is None else SCHOOLS[:LIMIT]
    print(f"  本次将处理: {len(targets)} / {len(SCHOOLS)} 所")

    summary = []
    pbar = tqdm(targets, desc="进度", unit="校")
    for school in pbar:
        pbar.set_postfix_str(school)

        # 已存在则跳过（支持断点续跑）
        out_file = OUTPUT_DIR / f"{safe_filename(school)}.json"
        if out_file.exists():
            tqdm.write(f"  [跳过] {school} 已有结果文件")
            with out_file.open("r", encoding="utf-8") as f:
                summary.append(json.load(f))
            continue

        result = process_school(school)

        # 单校落盘
        with out_file.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        n_pdf = len(result["pdfs"])
        err = f" [ERROR: {result['error']}]" if result["error"] else ""
        tqdm.write(f"  ✓ {school}: 域名={result['official_domain']}, "
                   f"PDF={n_pdf}{err}")

        summary.append(result)
        time.sleep(SCHOOL_SLEEP)

    # 汇总文件
    summary_file = OUTPUT_DIR / "_summary.json"
    with summary_file.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 终端简表
    print("\n" + "=" * 70)
    print("  最终汇总")
    print("=" * 70)
    total_pdf = 0
    for r in summary:
        n = len(r["pdfs"])
        total_pdf += n
        mark = "✗" if r["error"] else "✓"
        print(f"  {mark} {r['school']:<32} {n:>4} PDFs   ({r['official_domain']})")
    print("-" * 70)
    print(f"  合计: {total_pdf} 个 PDF")
    print(f"  汇总文件: {summary_file.resolve()}")


if __name__ == "__main__":
    main()