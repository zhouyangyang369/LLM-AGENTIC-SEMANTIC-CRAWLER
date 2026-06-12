"""
批量爬取编排器
从 university_units 表读取未覆盖大学列表，
并发/顺序调度 crawl_graph 对每所大学执行爬取。

使用方法:
    python -m src.pipeline.phase3.batch_crawler \
        --year 令和7年度 \
        --max-workers 3 \
        --limit 10
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from src.db.operations import get_uncovered_universities, get_coverage_stats
from src.pipeline.phase3.crawl_graph import crawl_university, _TAVILY_INTER_UNIV_SLEEP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_batch(
    target_year: str = "令和7年度",
    max_workers: int = 1,
    limit: int | None = None,
    university_filter: list[str] | None = None,
) -> dict:
    """
    批量爬取所有未覆盖大学。

    Args:
        target_year: 爬取目标年度
        max_workers: 并发线程数（建议 1~3，避免触发反爬）
        limit: 最多处理几所大学（None = 全部）
        university_filter: 指定大学名列表（None = 按未覆盖列表）

    Returns:
        {"total": int, "success": int, "failed": int, "skipped": int, "coverage_stats": dict}
    """
    # ── 获取任务列表 ─────────────────────────────────────────
    if university_filter:
        targets = [{"university_name": name, "prefecture": None} for name in university_filter]
        logger.info("指定大学模式: %d 所", len(targets))
    else:
        targets = get_uncovered_universities(target_year)
        logger.info("未覆盖大学: %d 所", len(targets))

    if limit:
        targets = targets[:limit]
        logger.info("限制处理: %d 所", len(targets))

    if not targets:
        logger.info("所有大学已覆盖，无需爬取 ✓")
        return {
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "coverage_stats": get_coverage_stats(target_year),
        }

    # ── 打印任务列表 ─────────────────────────────────────────
    logger.info("=== 爬取任务队列 (%d 所) ===", len(targets))
    for i, t in enumerate(targets[:10], 1):
        logger.info("  %d. %s (%s)", i, t["university_name"], t.get("prefecture", ""))
    if len(targets) > 10:
        logger.info("  ... 及其他 %d 所", len(targets) - 10)

    # ── 执行爬取 ─────────────────────────────────────────────
    stats = {"total": len(targets), "success": 0, "failed": 0, "skipped": 0}
    start_time = datetime.now()

    if max_workers <= 1:
        # 顺序执行
        for i, target in enumerate(targets, 1):
            name = target["university_name"]
            logger.info("── [%d/%d] %s ──", i, len(targets), name)
            try:
                final_state = crawl_university(name, target_year)
                coverage_results = final_state.get("coverage_results", [])
                processed_count = len(final_state.get("processed_urls", []))
                logger.info(
                    "[%s] 完成 | 处理 PDF: %d | 匹配 unit: %d",
                    name, processed_count, len(coverage_results)
                )
                stats["success"] += 1
            except Exception as e:
                logger.error("[%s] 爬取失败: %s", name, e)
                stats["failed"] += 1

            # 大学间冷却间隔，避免 Tavily 速率限制（最后一所不需要等待）
            if i < len(targets):
                logger.debug("大学间冷却等待 %.0fs...", _TAVILY_INTER_UNIV_SLEEP)
                time.sleep(_TAVILY_INTER_UNIV_SLEEP)
    else:
        # 并发执行
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(crawl_university, t["university_name"], target_year): t["university_name"]
                for t in targets
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    final_state = future.result()
                    coverage_results = final_state.get("coverage_results", [])
                    logger.info("[%s] 完成 | 匹配 unit: %d", name, len(coverage_results))
                    stats["success"] += 1
                except Exception as e:
                    logger.error("[%s] 爬取失败: %s", name, e)
                    stats["failed"] += 1

    # ── 最终覆盖率统计 ───────────────────────────────────────
    elapsed = (datetime.now() - start_time).total_seconds()
    coverage_stats = get_coverage_stats(target_year)

    logger.info(
        "\n=== 批量爬取完成 ===\n"
        "  总计: %d 所 | 成功: %d | 失败: %d\n"
        "  耗时: %.0f 秒\n"
        "  整体覆盖率: %d/%d (%.1f%%)",
        stats["total"], stats["success"], stats["failed"],
        elapsed,
        coverage_stats["covered"], coverage_stats["total"],
        coverage_stats["coverage_pct"],
    )

    stats["coverage_stats"] = coverage_stats
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="第三阶段批量爬取：Ground Truth 驱动"
    )
    parser.add_argument(
        "--year", default="令和7年度",
        help="目标年度（默认: 令和7年度）"
    )
    parser.add_argument(
        "--max-workers", type=int, default=1,
        help="并发线程数（默认: 1）"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="最多处理几所大学（默认: 全部）"
    )
    parser.add_argument(
        "--universities", nargs="+",
        help="指定大学名（空格分隔），不指定则处理所有未覆盖大学"
    )
    args = parser.parse_args()

    run_batch(
        target_year=args.year,
        max_workers=args.max_workers,
        limit=args.limit,
        university_filter=args.universities,
    )


if __name__ == "__main__":
    main()