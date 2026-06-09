"""
PDF 下载器
负责从 URL 下载 PDF 字节流，支持重试、超时、User-Agent 轮换。
"""
from __future__ import annotations

import logging
import time
import random
from typing import Optional
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 常用 User-Agent 池，避免被简单屏蔽
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


class PDFDownloadError(Exception):
    """PDF 下载失败"""


def _safe_url(url: str) -> str:
    """对包含日文/空格的 URL path/query 做安全编码。"""
    parts = urlsplit(url.strip())
    path = quote(parts.path, safe="/%:@")
    query = quote(parts.query, safe="=&%:@/?")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def _extract_first_pdf_link(html: str, base_url: str) -> Optional[str]:
    """从 HTML 页面中抽取第一个看起来像 PDF 的链接。"""
    soup = BeautifulSoup(html, "html.parser")
    scored_links: list[tuple[int, str]] = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href") or ""
        text = a_tag.get_text(" ", strip=True)
        absolute = urljoin(base_url, href)
        marker = f"{href} {text}".lower()
        if "pdf" not in marker and not absolute.lower().endswith(".pdf"):
            continue

        score = 0
        if absolute.lower().endswith(".pdf"):
            score += 10
        for keyword in ["募集要項", "入試要項", "学生募集", "admission", "entrance"]:
            if keyword.lower() in marker:
                score += 3
        scored_links.append((score, absolute))

    if not scored_links:
        return None
    scored_links.sort(key=lambda x: x[0], reverse=True)
    return scored_links[0][1]


def download_pdf(
    url: str,
    timeout: float = 30.0,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> bytes:
    """
    下载 PDF，返回原始字节流。

    Args:
        url: PDF 的完整 URL
        timeout: 请求超时（秒）
        max_retries: 最大重试次数
        retry_delay: 重试间隔基础秒数（实际会加随机抖动）

    Returns:
        PDF 原始字节

    Raises:
        PDFDownloadError: 重试耗尽或非 PDF 响应
    """
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "application/pdf,*/*",
        "Accept-Language": "ja,en;q=0.9",
    }

    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            request_url = _safe_url(url)
            logger.debug("下载 PDF [%d/%d]: %s", attempt, max_retries, request_url)
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(request_url, headers=headers)
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                if "pdf" in content_type.lower() or resp.content.startswith(b"%PDF"):
                    logger.info("下载成功: %s (%.1f KB)", request_url, len(resp.content) / 1024)
                    return resp.content

                # Tavily/搜索结果有时给的是招生 HTML 页面；尝试从页面内找 PDF 再下载一次。
                if "html" in content_type.lower() or "text" in content_type.lower():
                    pdf_link = _extract_first_pdf_link(resp.text, str(resp.url))
                    if pdf_link and pdf_link != request_url:
                        logger.info("从页面发现 PDF 链接: %s -> %s", request_url, pdf_link)
                        pdf_resp = client.get(_safe_url(pdf_link), headers=headers)
                        pdf_resp.raise_for_status()
                        pdf_content_type = pdf_resp.headers.get("content-type", "")
                        if "pdf" in pdf_content_type.lower() or pdf_resp.content.startswith(b"%PDF"):
                            logger.info("下载成功: %s (%.1f KB)", pdf_link, len(pdf_resp.content) / 1024)
                            return pdf_resp.content

                raise PDFDownloadError(
                    f"响应不是 PDF (content-type={content_type}): {request_url}"
                )

        except httpx.HTTPStatusError as e:
            last_error = e
            logger.warning("HTTP 错误 [%d/%d] %s: %s", attempt, max_retries, url, e)
        except httpx.RequestError as e:
            last_error = e
            logger.warning("请求错误 [%d/%d] %s: %s", attempt, max_retries, url, e)
        except PDFDownloadError:
            raise  # 非 PDF 不重试，直接抛出

        if attempt < max_retries:
            delay = retry_delay * attempt + random.uniform(0, 1)
            logger.debug("等待 %.1f 秒后重试...", delay)
            time.sleep(delay)

    raise PDFDownloadError(
        f"下载失败（{max_retries} 次重试耗尽）: {url} — 最后错误: {last_error}"
    )


def is_pdf_url(url: str) -> bool:
    """简单判断 URL 是否指向 PDF"""
    url_lower = url.lower()
    return (
        url_lower.endswith(".pdf")
        or "pdf" in url_lower
        or "/pdf/" in url_lower
    )