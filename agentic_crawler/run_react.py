#!/usr/bin/env python3
"""
ReAct Agent 実行エントリーポイント

使用方法:
    python agentic_crawler/run_react.py --school 室蘭工業大学
    python agentic_crawler/run_react.py --school 室蘭工業大学 --verbose

baseline（流水線版）との違い:
    - LLM が自律的にツールを選択・実行（ReAct パターン）
    - 固定フローではなく LLM が戦略を決定
    - run_single.py は baseline 版（比較用に保持）
"""

import argparse
import json
import logging
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from agent.react.graph import build_react_graph, ReactState
from tools.university_loader import get_university


def main():
    parser = argparse.ArgumentParser(description="ReAct Agent — 募集要項 PDF 収集")
    parser.add_argument("--school", required=True, help="大学名（例: 室蘭工業大学）")
    parser.add_argument("--verbose", action="store_true", help="デバッグログを表示")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 不要なログを抑制
    for noisy in ["httpcore", "httpx", "openai._base_client", "urllib3"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # 学校情報を読み込む
    school_info = get_university(args.school)
    if not school_info:
        print(f"[ERROR] 学校が見つかりません: {args.school}")
        sys.exit(1)

    official_url = school_info.official_url
    domain = school_info.domain

    print(f"\n{'='*60}")
    print(f"  ReAct Agent: {args.school}")
    print(f"  URL: {official_url}")
    print(f"{'='*60}\n")

    # グラフ実行
    graph = build_react_graph()
    initial_state = ReactState(
        school_name=args.school,
        official_url=official_url,
        domain=domain,
    )

    raw = graph.invoke(initial_state)
    final_state = ReactState(**raw) if isinstance(raw, dict) else raw

        # 結果表示（Windows cp932 で表示できない文字を ? に置換）
    def safe_print(text: str):
        print(text.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8', errors='replace'))

    pdfs = final_state.collected_pdfs
    safe_print(f"\n{'='*60}")
    safe_print(f"  結果: {len(pdfs)} 件の PDF を収集（ReAct Agent）")
    safe_print(f"  ステップ数: {final_state.step_count}")
    safe_print(f"{'='*60}")
    for p in pdfs:
        safe_print(f"  {p.get('text', '?')[:60]}")
        safe_print(f"    → {p.get('url', '')}")

    # JSON 保存
    out_dir = Path(__file__).parent / "results" / args.school
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.school}_react.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "school": args.school,
            "official_url": official_url,
            "step_count": final_state.step_count,
            "pdfs": pdfs,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  出力: {out_path}\n")


if __name__ == "__main__":
    main()
