"""
Tavily 検索ツール — Agentic クローラーのフォールバック検索エンジン。
"""

import logging
import random
import time

from tavily import TavilyClient

from config import TAVILY_API_KEY, TAVILY_SLEEP, MAX_RETRY, RETRY_BACKOFF

logger = logging.getLogger(__name__)

_client: TavilyClient | None = None


def _get_client() -> TavilyClient:
    global _client
    if _client is None:
        _client = TavilyClient(TAVILY_API_KEY)
    return _client


def tavily_search(
    query: str,
    max_results: int = 5,
    search_depth: str = "advanced",
    domain_filter: str = "",
) -> list[dict]:
    """
    Tavily で検索して結果リストを返す。
    Returns: [{"url": ..., "title": ..., "content": ...}, ...]
    """
    if domain_filter:
        query = f"{query} site:{domain_filter}"

    client = _get_client()
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = client.search(
                query=query,
                search_depth=search_depth,
                max_results=max_results,
            )
            time.sleep(TAVILY_SLEEP)
            return resp.get("results", [])
        except Exception as e:
            wait = RETRY_BACKOFF ** attempt + random.uniform(0, 1)
            logger.warning(f"Tavily failed (attempt {attempt}/{MAX_RETRY}): {e}. Retry in {wait:.1f}s")
            time.sleep(wait)

    return []


def tavily_search_admission(school: str, department: str = "", domain: str = "") -> list[dict]:
    """入試・募集要項に特化した検索クエリを生成して検索"""
    queries = [
        f"{school} {department} 募集要項 2025".strip(),
        f"{school} {department} 大学院入試 募集要項".strip(),
        f"{school} {department} 入学者選抜要項".strip(),
    ]

    seen_urls: set[str] = set()
    all_results: list[dict] = []

    for query in queries:
        results = tavily_search(query, max_results=5, domain_filter=domain)
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)

    return all_results
