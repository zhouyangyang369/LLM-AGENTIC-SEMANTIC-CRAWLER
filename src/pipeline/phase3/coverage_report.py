"""
覆盖率报告生成器
查询 Supabase，输出覆盖率统计表格（终端 + CSV 两种格式）。

使用方法:
    python -m src.pipeline.phase3.coverage_report
    python -m src.pipeline.phase3.coverage_report --output report.csv
    python -m src.pipeline.phase3.coverage_report --uncovered-only
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from src.db.operations import (
    get_coverage_stats,
    get_per_university_coverage,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 终端表格列宽配置
COL_WIDTHS = {
    "大学名": 20,
    "都道府県": 5,
    "total": 6,
    "covered": 7,
    "coverage%": 9,
}


def print_summary(stats: dict):
    """打印整体覆盖率摘要"""
    bar_len = 40
    filled = int(stats["coverage_pct"] / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)

    print(f"\n{'='*60}")
    print(f"  📊 覆盖率レポート ({stats['target_year']})")
    print(f"{'='*60}")
    print(f"  対象 unit 総数: {stats['total']:>6,}")
    print(f"  カバー済み:     {stats['covered']:>6,}")
    print(f"  未カバー:       {stats['uncovered']:>6,}")
    print(f"  覆盖率:         {stats['coverage_pct']:>5.1f}%  [{bar}]")
    print(f"{'='*60}\n")


def print_per_university_table(rows: list[dict], uncovered_only: bool = False):
    """打印每所大学的覆盖率表格"""
    if uncovered_only:
        rows = [r for r in rows if r["coverage_pct"] < 100.0]
        print(f"  （未完全覆盖的大学: {len(rows)} 所）\n")

    # 表头
    header = (
        f"{'大学名':<20} {'都道府県':<5} {'total':>6} {'covered':>7} {'coverage%':>9}"
    )
    print(header)
    print("-" * len(header))

    for r in rows:
        pct = r["coverage_pct"]
        # 颜色指示（终端 ANSI）
        if pct >= 100:
            status = "✓"
        elif pct >= 50:
            status = "△"
        else:
            status = "✗"

        print(
            f"{r['university_name']:<20} "
            f"{(r['prefecture'] or ''):<5} "
            f"{r['total_units']:>6} "
            f"{r['covered_units']:>7} "
            f"{pct:>8.1f}% {status}"
        )


def save_csv(rows: list[dict], output_path: Path):
    """导出 CSV"""
    fieldnames = ["university_name", "prefecture", "total_units", "covered_units", "coverage_pct"]
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    logger.info("CSV 已保存: %s", output_path)


def main():
    parser = argparse.ArgumentParser(description="覆盖率レポート出力")
    parser.add_argument("--year", default="令和7年度", help="目标年度")
    parser.add_argument("--output", help="CSV 输出路径（可选）")
    parser.add_argument(
        "--uncovered-only", action="store_true",
        help="只显示未完全覆盖的大学"
    )
    args = parser.parse_args()

    # 整体统计
    stats = get_coverage_stats(args.year)
    print_summary(stats)

    # 每所大学详情
    rows = get_per_university_coverage(args.year)
    print_per_university_table(rows, uncovered_only=args.uncovered_only)

    # CSV 导出
    if args.output:
        save_csv(rows, Path(args.output))


if __name__ == "__main__":
    main()