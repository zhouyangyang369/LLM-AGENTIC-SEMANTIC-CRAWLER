#!/usr/bin/env python3
"""
単一大学をデバッグ実行するエントリポイント。

使用例:
  cd agentic_crawler
  ../venv/bin/python run_single.py --school 東京大学
  ../venv/bin/python run_single.py --school 東北大学 --verbose
"""

import argparse
import json
import logging
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from config import OUTPUT_DIR, TARGET_TYPES
from tools.university_loader import load_universities, UniversityInfo
from agent.schemas import AgentState, SchoolResult
from agent.graph import get_graph


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def run_school(uni: UniversityInfo, verbose: bool = False) -> SchoolResult:
    graph = get_graph()

    initial_state = AgentState(
        school_name=uni.name,
        official_url=uni.official_url,
        sitemap_url=uni.sitemap_url,
        domain=uni.domain,
    )

    print(f"\n{'='*60}")
    print(f"  学校: {uni.name}")
    print(f"  官網: {uni.official_url}")
    print(f"  Sitemap: {uni.sitemap_url}")
    print(f"{'='*60}\n")

    raw = graph.invoke(initial_state)
    # LangGraph returns a dict; reconstruct AgentState for typed access
    final_state = AgentState(**raw) if isinstance(raw, dict) else raw

    result = SchoolResult(
        school=final_state.school_name,
        official_url=final_state.official_url,
        domain=final_state.domain,
        sitemap_url=final_state.sitemap_url,
        candidate_pages=final_state.candidate_pages,
        discovered_subsites=final_state.discovered_subsites,
        pdfs=final_state.pdfs,
        found_departments=final_state.found_departments,
        missing_departments=final_state.missing_departments,
        is_complete=final_state.is_complete,
        errors=final_state.errors,
        decision_trace=final_state.decision_trace,
    )

    return result


def save_result(result: SchoolResult, out_dir: Path) -> Path:
    safe_name = result.school.replace("/", "_").replace(" ", "_")
    out_file = out_dir / f"{safe_name}.json"
    out_file.write_text(
        result.model_dump_json(indent=2, exclude={"decision_trace"}),
        encoding="utf-8",
    )
    trace_file = out_dir / f"{safe_name}_trace.jsonl"
    with trace_file.open("w", encoding="utf-8") as f:
        for entry in result.decision_trace:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return out_file


def main() -> None:
    parser = argparse.ArgumentParser(description="単一大学の募集要項 PDF を収集")
    parser.add_argument("--school", required=True, help="大学名（日本語）")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)

    universities = load_universities(TARGET_TYPES)
    uni = next((u for u in universities if u.name == args.school), None)

    if uni is None:
        print(f"ERROR: '{args.school}' が見つかりません。")
        print("利用可能な学校名:")
        for u in universities[:20]:
            print(f"  - {u.name}")
        sys.exit(1)

    out_dir = OUTPUT_DIR / uni.name
    out_dir.mkdir(parents=True, exist_ok=True)

    result = run_school(uni, verbose=args.verbose)
    out_file = save_result(result, out_dir)

    print(f"\n{'='*60}")
    print(f"  結果: {len(result.pdfs)} 件の PDF を収集")
    print(f"  研究科: {result.found_departments}")
    print(f"  未収集: {result.missing_departments}")
    print(f"  完備性: {'✓' if result.is_complete else '△'}")
    print(f"  出力: {out_file}")
    print(f"{'='*60}\n")

    # PDF 一覧を表示
    for pdf in result.pdfs:
        print(f"  [{pdf.category}] {pdf.department or '?'} | {pdf.text[:50]} | {pdf.url}")


if __name__ == "__main__":
    main()
