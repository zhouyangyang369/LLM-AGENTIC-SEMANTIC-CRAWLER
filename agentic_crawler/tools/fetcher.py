"""
ページフェッチャー — URL を取得して Markdown テキストに変換する。
キャッシュ付き（results/_page_cache/ に保存）。

crawl4ai が利用可能な場合はそれを使用（JS レンダリング対応）。
失敗時は requests + BeautifulSoup にフォールバック。
"""

import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import REQUEST_TIMEOUT, CACHE_DIR

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AdmissionCrawler/1.0; research)"}


def _url_to_cache_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _load_cache(url: str) -> str | None:
    path = CACHE_DIR / f"{_url_to_cache_key(url)}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _save_cache(url: str, content: str) -> None:
    path = CACHE_DIR / f"{_url_to_cache_key(url)}.md"
    path.write_text(content, encoding="utf-8")


def _html_to_markdown(html: str, base_url: str) -> str:
    """BeautifulSoup でシンプルな Markdown 変換（リンク付き）"""
    soup = BeautifulSoup(html, "html.parser")

    # 不要タグを除去
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
        tag.decompose()

    lines = []
    for elem in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "a"]):
        tag = elem.name
        text = elem.get_text(strip=True)
        if not text:
            continue

        if tag in ("h1", "h2", "h3", "h4"):
            level = int(tag[1])
            lines.append(f"{'#' * level} {text}")
        elif tag == "li":
            lines.append(f"- {text}")
        elif tag == "a":
            href = elem.get("href", "")
            if href:
                full_url = urljoin(base_url, href)
                lines.append(f"[{text}]({full_url})")
        elif tag == "p":
            lines.append(text)

    return "\n".join(lines)


def fetch_page_as_markdown(url: str, use_cache: bool = True) -> str:
    """URL を取得して Markdown 文字列を返す。失敗時は空文字列。"""
    if use_cache:
        cached = _load_cache(url)
        if cached is not None:
            logger.debug(f"Cache hit: {url}")
            return cached

    # まず requests で試みる
    content = _fetch_with_requests(url)

    if content:
        if use_cache:
            _save_cache(url, content)
        return content

    logger.warning(f"Failed to fetch: {url}")
    return ""


def _fetch_with_requests(url: str) -> str:
    """requests + BeautifulSoup でページを取得して Markdown に変換"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.encoding = resp.apparent_encoding or "utf-8"
        if resp.status_code != 200:
            return ""
        return _html_to_markdown(resp.text, url)
    except Exception as e:
        logger.debug(f"requests fetch failed: {url} — {e}")
        return ""


def extract_links_from_markdown(markdown: str, base_url: str) -> list[dict]:
    """
    Markdown テキストからリンクを抽出。
    Returns: [{"text": ..., "url": ...}, ...]
    """
    pattern = re.compile(r"\[([^\]]*)\]\((https?://[^\)]+)\)")
    links = []
    seen = set()
    for text, url in pattern.findall(markdown):
        if url not in seen:
            seen.add(url)
            links.append({"text": text.strip(), "url": url.strip()})
    return links


def extract_pdf_links_from_markdown(markdown: str) -> list[dict]:
    """Markdown から PDF リンクのみを抽出"""
    all_links = extract_links_from_markdown(markdown, "")
    return [
        lnk for lnk in all_links
        if lnk["url"].lower().split("?")[0].endswith(".pdf")
    ]


def is_same_domain(url: str, domain: str) -> bool:
    """URL がドメインと同じホストかどうか"""
    host = urlparse(url).netloc.lstrip("www.")
    return domain in host
