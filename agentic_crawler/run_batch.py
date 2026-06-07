#!/usr/bin/env python3
"""
全大学（または指定タイプ）を一括実行するエントリポイント。
断点続跑: 結果ファイルが既に存在する学校はスキップ。

使用例:
  cd agentic_crawler
  ../venv/bin/python run_batch.py                     # 全国立大学
  ../venv/bin/python run_batch.py --limit 3           # 先頭 3 校のみ
  ../venv/bin/python run_batch.py --school 東京大学   # 1 校だけ再実行
  ../venv/bin/python run_batch.py --types national public
"""

import argparse
import json
import logging
import sys
import os
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from tqdm import tqdm

from config import OUTPUT_DIR, SCHOOL_SLEEP, TARGET_TYPES
from tools.university_loader import load_universities
from agent.schemas import SchoolResult
from agent.graph import get_graph
from agent.schemas import AgentState
from run_single import run_school, save_result, setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="日本大学 募集要項 PDF 一括収集")
    parser.add_argument("--limit",  type=int, default=None, help="先頭 N 校のみ実行")
    parser.add_argument("--school", type=str, default=None, help="特定の学校のみ再実行")
    parser.add_argument("--types",  nargs="+", default=None, help="national / public / private")
    parser.add_argument("--force",  action="store_true", help="既存結果を上書き")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    types = args.types or TARGET_TYPES
    universities = load_universities(types)

    if args.school:
        universities = [u for u in universities if u.name == args.school]
        if not universities:
            print(f"ERROR: '{args.school}' が見つかりません。")
            sys.exit(1)

    if args.limit:
        universities = universities[:args.limit]

    print(f"\n{'='*70}")
    print(f"  日本大学 募集要項 PDF 一括収集")
    print(f"  対象: {len(universities)} 校  types={types}")
    print(f"  出力: {OUTPUT_DIR.resolve()}")
    print(f"{'='*70}\n")

    summary: list[dict] = []
    skipped = 0
    errors = 0

    pbar = tqdm(universities, desc="進捗", unit="校")
    for uni in pbar:
        pbar.set_postfix_str(uni.name[:20])

        out_dir = OUTPUT_DIR / uni.name
        result_file = out_dir / f"{uni.name.replace('/', '_')}.json"

        # 断点続跑: 既存ファイルがあればスキップ
        if result_file.exists() and not args.force:
            tqdm.write(f"  [スキップ] {uni.name} (既存)")
            skipped += 1
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
                summary.append(data)
            except Exception:
                pass
            continue

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            result = run_school(uni, verbose=args.verbose)
            save_result(result, out_dir)
            n_pdf = len(result.pdfs)
            complete_mark = "✓" if result.is_complete else "△"
            tqdm.write(f"  {complete_mark} {uni.name}: {n_pdf} PDFs  "
                       f"depts={len(result.found_departments)}")
            summary.append(result.model_dump(exclude={"decision_trace"}))
        except Exception as e:
            tqdm.write(f"  ✗ {uni.name}: ERROR — {e}")
            logger.exception(f"Failed: {uni.name}")
            errors += 1

        time.sleep(SCHOOL_SLEEP)

    # 全体サマリ保存
    summary_file = OUTPUT_DIR / "_summary.json"
    summary_file.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 集計表示
    total_pdf = sum(len(r.get("pdfs", [])) for r in summary)
    print(f"\n{'='*70}")
    print(f"  最終サマリ")
    print(f"{'='*70}")
    for r in summary:
        n = len(r.get("pdfs", []))
        mark = "✓" if r.get("is_complete") else "△"
        print(f"  {mark} {r.get('school','?'):<30} {n:>4} PDFs")
    print(f"{'-'*70}")
    print(f"  合計: {total_pdf} PDFs  スキップ: {skipped}  エラー: {errors}")
    print(f"  サマリ: {summary_file.resolve()}")


if __name__ == "__main__":
    main()
