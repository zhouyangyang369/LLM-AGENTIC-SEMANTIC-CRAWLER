"""
Sitemap パーサー — sitemap.xml / sitemap index を再帰展開して URL 一覧を返す。
robots.txt からの sitemap URL 探索もサポート。
"""

import logging
import re
import time
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests

from config import REQUEST_TIMEOUT, MAX_SITEMAP_URLS

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AdmissionCrawler/1.0; research)"}
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _fetch_text(url: str, timeout: int = REQUEST_TIMEOUT) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.encoding = resp.apparent_encoding or "utf-8"
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        logger.debug(f"Fetch failed: {url} — {e}")
    return None


def _parse_sitemap_xml(content: str) -> tuple[list[str], list[str]]:
    """
    Returns:
        (page_urls, child_sitemap_urls)
    """
    page_urls: list[str] = []
    child_sitemaps: list[str] = []
    try:
        root = ET.fromstring(content)
        tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

        if tag == "sitemapindex":
            for sm in root.iter(f"{{{SITEMAP_NS}}}loc"):
                child_sitemaps.append(sm.text.strip())
        else:
            for loc in root.iter(f"{{{SITEMAP_NS}}}loc"):
                page_urls.append(loc.text.strip())
    except ET.ParseError:
        # プレーンテキスト形式のサイトマップにフォールバック
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("http"):
                page_urls.append(line)
    return page_urls, child_sitemaps


def _parse_html_sitemap(content: str, base_url: str) -> list[str]:
    """HTML 形式のサイトマップページからリンクを抽出"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(content, "html.parser")
    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("http"):
            urls.append(href)
        elif href.startswith("/"):
            urls.append(urljoin(base_url, href))
    return urls


def fetch_sitemap_urls(sitemap_url: str, visited: set[str] | None = None) -> list[str]:
    """
    sitemap_url を再帰的に展開して全 URL を返す。
    sitemap index → 各子 sitemap → ページ URL の順に展開。
    """
    if visited is None:
        visited = set()
    if sitemap_url in visited:
        return []
    visited.add(sitemap_url)

    content = _fetch_text(sitemap_url)
    if not content:
        logger.warning(f"Could not fetch sitemap: {sitemap_url}")
        return []

    # XML かどうか判定
    stripped = content.lstrip()
    if stripped.startswith("<?xml") or stripped.startswith("<urlset") or stripped.startswith("<sitemapindex"):
        page_urls, child_sitemaps = _parse_sitemap_xml(content)
        # 子サイトマップを再帰展開
        for child_url in child_sitemaps:
            if len(page_urls) >= MAX_SITEMAP_URLS:
                break
            page_urls.extend(fetch_sitemap_urls(child_url, visited))
    else:
        # HTML サイトマップとして処理
        page_urls = _parse_html_sitemap(content, sitemap_url)

    # 重複除去・上限適用
    seen = set()
    result = []
    for u in page_urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
        if len(result) >= MAX_SITEMAP_URLS:
            break

    logger.info(f"Sitemap {sitemap_url}: {len(result)} URLs found")
    return result


def find_sitemap_from_robots(base_url: str) -> list[str]:
    """robots.txt から Sitemap: 行を探す"""
    robots_url = urljoin(base_url, "/robots.txt")
    content = _fetch_text(robots_url)
    if not content:
        return []
    sitemaps = []
    for line in content.splitlines():
        if line.lower().startswith("sitemap:"):
            url = line.split(":", 1)[1].strip()
            if url.startswith("http"):
                sitemaps.append(url)
    return sitemaps


def discover_sitemap(official_url: str, provided_sitemap_url: str = "") -> list[str]:
    """
    優先順位:
    1. provided_sitemap_url（Excel で提供）
    2. /sitemap.xml 試行
    3. robots.txt から探索
    4. 共通パターン試行
    全て失敗したら空リストを返す。
    """
    candidates: list[str] = []

    if provided_sitemap_url:
        candidates.append(provided_sitemap_url)

    parsed = urlparse(official_url or provided_sitemap_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    for path in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap/", "/sitemap.html"]:
        candidates.append(urljoin(base, path))

    # robots.txt
    candidates.extend(find_sitemap_from_robots(base))

    for url in candidates:
        content = _fetch_text(url)
        if content and len(content) > 100:
            logger.info(f"Sitemap found: {url}")
            return fetch_sitemap_urls(url)

    logger.warning(f"No sitemap found for {official_url}")
    return []
