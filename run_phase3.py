"""
第三阶段主入口
Ground Truth 驱动爬取流水线

子命令:
  download-excel   从文部科学省下载全国大学一覧 Excel
  import-excel     解析 Excel → 导入 university_units 表
  crawl            爬取单所或批量大学的募集要項 PDF
  report           输出覆盖率报告

使用示例:
  # 1. 下载文科省 Excel
  python run_phase3.py download-excel

  # 2. 导入 ground truth（可先 --dry-run 检查）
  python run_phase3.py import-excel --excel data/R06_daigaku.xlsx --dry-run
  python run_phase3.py import-excel --excel data/R06_daigaku.xlsx

  # 3. 爬取指定大学
  python run_phase3.py crawl --universities 北海道大学 東北大学

  # 4. 批量爬取所有未覆盖大学
  python run_phase3.py crawl --max-workers 2 --limit 50

  # 5. 查看覆盖率报告
  python run_phase3.py report
  python run_phase3.py report --uncovered-only --output report.csv
"""
from __future__ import annotations

import argparse
import sys
import logging

from src.utils.logger import setup_logging


def configure_stdio_encoding() -> None:
    """在 Windows/cp932 等控制台中安全输出中日文。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def cmd_download_excel(args):
    from src.pipeline.phase3.mext_downloader import download_mext_excel
    from pathlib import Path
    output = Path(args.output) if args.output else None
    saved = download_mext_excel(output)
    print(f"✓ 保存先: {saved}")


def cmd_import_excel(args):
    from src.pipeline.phase3.mext_importer import import_excel_to_db
    stats = import_excel_to_db(
        excel_path=args.excel,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )
    print(f"\n導入結果: 総計={stats['total']} | 新規={stats['inserted']} | 更新={stats['updated']} | エラー={stats['errors']}")


def cmd_crawl(args):
    if args.universities:
        # 指定大学モード
        from src.pipeline.phase3.batch_crawler import run_batch
        stats = run_batch(
            target_year=args.year,
            max_workers=args.max_workers,
            university_filter=args.universities,
        )
    else:
        # 全量未覆盖モード
        from src.pipeline.phase3.batch_crawler import run_batch
        stats = run_batch(
            target_year=args.year,
            max_workers=args.max_workers,
            limit=args.limit,
        )
    cov = stats.get("coverage_stats", {})
    print(f"\n爬取完了: 成功={stats['success']} | 失敗={stats['failed']}")
    print(f"覆盖率: {cov.get('covered',0)}/{cov.get('total',0)} ({cov.get('coverage_pct',0)}%)")


def cmd_report(args):
    from src.pipeline.phase3.coverage_report import (
        get_coverage_stats, get_per_university_coverage,
        print_summary, print_per_university_table, save_csv
    )
    from pathlib import Path

    stats = get_coverage_stats(args.year)
    print_summary(stats)

    rows = get_per_university_coverage(args.year)
    print_per_university_table(rows, uncovered_only=args.uncovered_only)

    if args.output:
        save_csv(rows, Path(args.output))


def main():
    configure_stdio_encoding()

    parser = argparse.ArgumentParser(
        description="第三阶段: Ground Truth 驱動爬取パイプライン",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル（默认: INFO）",
    )
    parser.add_argument(
        "--log-file", default=None,
        help="ログファイルパス（例: logs/phase3.log）",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── download-excel ─────────────────────────────────────
    p_dl = subparsers.add_parser("download-excel", help="文科省 Excel をダウンロード")
    p_dl.add_argument("--output", help="保存パス（例: data/R06.xlsx）")
    p_dl.set_defaults(func=cmd_download_excel)

    # ── import-excel ───────────────────────────────────────
    p_imp = subparsers.add_parser("import-excel", help="Excel → university_units 导入")
    p_imp.add_argument("--excel", required=True, help="Excel ファイルパス")
    p_imp.add_argument("--dry-run", action="store_true", help="试运行，不写数据库")
    p_imp.add_argument("--batch-size", type=int, default=100)
    p_imp.set_defaults(func=cmd_import_excel)

    # ── crawl ──────────────────────────────────────────────
    p_crawl = subparsers.add_parser("crawl", help="募集要項 PDF 爬取")
    p_crawl.add_argument("--year", default="令和7年度", help="目标年度")
    p_crawl.add_argument("--max-workers", type=int, default=1, help="并发数")
    p_crawl.add_argument("--limit", type=int, help="最多处理几所大学")
    p_crawl.add_argument(
        "--universities", nargs="+",
        help="指定大学名（例: 北海道大学 東北大学）"
    )
    p_crawl.set_defaults(func=cmd_crawl)

    # ── report ─────────────────────────────────────────────
    p_rep = subparsers.add_parser("report", help="覆盖率レポート")
    p_rep.add_argument("--year", default="令和7年度")
    p_rep.add_argument("--uncovered-only", action="store_true", help="只显示未完全覆盖的大学")
    p_rep.add_argument("--output", help="CSV 出力パス")
    p_rep.set_defaults(func=cmd_report)

    args = parser.parse_args()

    # 配置日志
    setup_logging(level=args.log_level, log_file=args.log_file)

    # 执行子命令
    args.func(args)


if __name__ == "__main__":
    main()