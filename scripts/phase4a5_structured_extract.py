# -*- coding: utf-8 -*-
"""
Phase 4A.5: full_text 全文から構造化データ抽出（分級処理方式）

文字数に応じて3段階で処理する。LLM 呼び出し回数を大幅削減（従来比 -90%）。

  <= 15,000字  : SHORT       - 全文をそのまま1回で抽出
  <= 60,000字  : MEDIUM      - 前半12,000字 + 後半3,000字 -> 1回抽出
  >  60,000字  : LONG        - キーワードページを検出して抜粋 -> 1~2回

使用方法:
  python scripts/phase4a5_structured_extract.py --dry-run
  python scripts/phase4a5_structured_extract.py
  python scripts/phase4a5_structured_extract.py --universities 北海道大学 東北大学
  python scripts/phase4a5_structured_extract.py --limit 5
  python scripts/phase4a5_structured_extract.py --reprocess
  python scripts/phase4a5_structured_extract.py --all
"""
import sys
import os
import json
import re
import time
import argparse
from typing import Optional

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "agentic_crawler")
))

from dotenv import load_dotenv
load_dotenv()

# ============================================================
# 設定
# ============================================================
THRESH_SHORT  = 15_000
THRESH_MEDIUM = 60_000
MEDIUM_HEAD   = 12_000
MEDIUM_TAIL   =  3_000
LONG_PAGE_WINDOW = 3
LONG_MAX_CHARS   = 15_000

KEY_PAGE_KEYWORDS = [
    "出願期間", "出願資格", "試験日", "合格発表", "入学手続",
    "募集人員", "募集定員", "選抜方法", "試験科目", "配点",
    "検定料", "出願書類", "一般選抜", "学校推薦型", "総合型選抜",
]

SLEEP_BETWEEN_PDFS = 1.5
SLEEP_BETWEEN_LLM  = 0.5

EXP_UNIVERSITIES = [
    "山形大学", "大阪大学", "福島大学", "横浜国立大学",
    "名古屋工業大学", "上越教育大学", "旭川医科大学",
    "北見工業大学", "東京外国語大学", "金沢大学",
]

# ============================================================
# プロンプト
# ============================================================
EXTRACT_PROMPT = (
    "あなたは日本の大学入試募集要項から情報を抽出する専門家です。\n"
    "以下のテキストから入試に関するすべての情報を抽出してください。\n\n"
    "【大学名】{university_name}\n"
    "【学部/研究科】{unit_name}\n"
    "【テキスト（{text_label}）】\n"
    "{text}\n\n"
    "以下のJSON形式で抽出してください。\n"
    "- 情報が見つからない場合はそのフィールドを省略（nullではなく省略）\n"
    "- 複数の入試方式がある場合はすべて抽出\n"
    "- 日付は原文表記のまま\n"
    "- 定員は数字のみ\n\n"
    "{{\n"
    '  "exam_types": [\n'
    "    {{\n"
    '      "type": "入試方式名",\n'
    '      "target": "対象学部・学科・専攻",\n'
    '      "application_period": {{\n'
    '        "start": "出願開始日",\n'
    '        "end": "出願締切日",\n'
    '        "notes": "消印有効等"\n'
    "      }},\n"
    '      "exam_date": "試験実施日",\n'
    '      "result_date": "合格発表日",\n'
    '      "enrollment_deadline": "入学手続締切日",\n'
    '      "capacity": 募集人員の数字,\n'
    '      "exam_subjects": [\n'
    '        {{"subject": "科目名", "score": 配点数字, "notes": "備考"}}\n'
    "      ],\n"
    '      "qualification": "出願資格の概要",\n'
    '      "application_documents": ["調査書", "志願票"],\n'
    '      "notes": "その他特記事項"\n'
    "    }}\n"
    "  ],\n"
    '  "general_info": {{\n'
    '    "academic_year": "対象年度（例：令和7年度）",\n'
    '    "university_name": "{university_name}",\n'
    '    "notes": "全体的な特記事項"\n'
    "  }}\n"
    "}}\n\n"
    "JSONのみ出力してください。説明文・コードブロック記号は不要です。"
)

MERGE_PROMPT = (
    "以下は同じPDFの前半・後半から抽出された入試情報です。\n"
    "マージして重複を排除し、最も完全な情報にまとめてください。\n\n"
    "【大学名】{university_name}\n"
    "【前半の抽出結果】\n"
    "{result1}\n\n"
    "【後半の抽出結果】\n"
    "{result2}\n\n"
    "ルール:\n"
    "- 同じ入試方式は1つにまとめ、情報が多い方を優先する\n"
    "- 日付・定員など具体的な数値は必ず保持する\n"
    "- JSONのみ出力（説明不要）\n"
)

# ============================================================
# JSON パース
# ============================================================
def _parse_json_response(response: str) -> Optional[dict]:
    text = response.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    candidate = match.group()
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


# ============================================================
# テキスト準備（分級処理）
# ============================================================
def _split_pages(full_text: str) -> list:
    parts = re.split(r"--- Page (\d+) ---", full_text)
    if len(parts) <= 1:
        return [(0, full_text.strip())]
    pages = []
    i = 1
    while i < len(parts) - 1:
        page_no = int(parts[i])
        page_text = parts[i + 1].strip()
        if page_text:
            pages.append((page_no, page_text))
        i += 2
    return pages if pages else [(0, full_text.strip())]


def prepare_text_for_extraction(full_text: str, university_name: str) -> tuple:
    """全文をそのまま送信する（全文モード）"""
    return [full_text], "FULLTEXT"


# ============================================================
# LLM 抽出
# ============================================================
def extract_one(llm_call, text: str, university_name: str,
                unit_name: str, text_label: str = "全文") -> Optional[dict]:
    prompt = EXTRACT_PROMPT.format(
        university_name=university_name,
        unit_name=unit_name or "全学部・全研究科",
        text=text,
        text_label=text_label,
    )
    try:
        response = llm_call(prompt, max_tokens=8192)
        result = _parse_json_response(response)
        if result is None:
            print("    [DEBUG] JSON parse failed. Response head:")
            print("    " + repr(response[:400]))
        return result
    except Exception as e:
        print("    [DEBUG] LLM error: {}".format(e))
        return None


def merge_two_results(llm_call, result1: dict, result2: dict,
                      university_name: str) -> dict:
    prompt = MERGE_PROMPT.format(
        university_name=university_name,
        result1=json.dumps(result1, ensure_ascii=False, indent=2)[:5000],
        result2=json.dumps(result2, ensure_ascii=False, indent=2)[:5000],
    )
    try:
        response = llm_call(prompt, max_tokens=8192)
        merged = _parse_json_response(response)
        if merged:
            return merged
    except Exception:
        pass
    all_exam_types = result1.get("exam_types", []) + result2.get("exam_types", [])
    general_info = {**result1.get("general_info", {}), **result2.get("general_info", {})}
    seen, deduped = set(), []
    for et in all_exam_types:
        key = et.get("type", "")
        if key and key not in seen:
            seen.add(key)
            deduped.append(et)
        elif not key:
            deduped.append(et)
    return {"exam_types": deduped, "general_info": general_info}


# ============================================================
# メイン
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Phase 4A.5: full_text全文からLLM構造化抽出（分級処理方式）"
    )
    parser.add_argument("--dry-run", action="store_true", help="確認のみ（変更なし）")
    parser.add_argument("--universities", nargs="+", help="対象大学名（省略時は実験用10大学）")
    parser.add_argument("--all", action="store_true", help="full_textありの全大学を対象")
    parser.add_argument("--limit", type=int, help="処理件数上限")
    parser.add_argument("--reprocess", action="store_true", help="処理済みも再処理")
    parser.add_argument("--doc-types", nargs="+",
                        default=["募集要項", "選抜要項", "出願要領"])
    args = parser.parse_args()

    from src.db.supabase_client import get_supabase
    from llm.client import llm_call
    client = get_supabase()

    if args.all:
        target_universities = None
    elif args.universities:
        target_universities = args.universities
    else:
        target_universities = EXP_UNIVERSITIES

    mode_str = "[DRY-RUN] " if args.dry_run else ""
    print("{}Phase 4A.5: 構造化データ抽出 開始（分級処理方式）".format(mode_str))
    print("  対象 doc_type  : {}".format(args.doc_types))
    print("  対象大学       : {}".format(target_universities or "全大学"))
    print("=" * 70)

    all_records = []
    page_size = 100
    offset = 0
    while True:
        q = (
            client.table("crawled_pdfs")
            .select("id,university_name,pdf_url,pdf_scope,actual_year,"
                    "academic_year,extracted_units,full_text,char_count,doc_type")
            .eq("is_excluded", False)
            .not_.is_("full_text", "null")
            .in_("doc_type", args.doc_types)
            .range(offset, offset + page_size - 1)
        )
        if target_universities:
            q = q.in_("university_name", target_universities)
        if not args.reprocess:
            q = q.is_("structured_data", "null")
        r = q.execute()
        if not r.data:
            break
        all_records.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size

    if args.limit:
        all_records = all_records[:args.limit]
    print("対象: {} 件\n".format(len(all_records)))

    if args.dry_run:
        mode_counts = {}
        total_llm_calls = 0
        print("[DRY-RUN] 処理モード内訳（全{}件）:\n".format(len(all_records)))
        print("  {:<20} {:<8} {:>10}  {:<16} {}".format(
            "大学名", "doc_type", "字数", "モード", "LLM呼出"))
        print("  " + "-" * 62)
        for rec in all_records:
            ft = rec.get("full_text", "") or ""
            texts, mode = prepare_text_for_extraction(ft, rec["university_name"])
            llm_n = len(texts) + (1 if len(texts) > 1 else 0)
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
            total_llm_calls += llm_n
            print("  {:<20} {:<8} {:>10,}字  {:<16} {}回".format(
                rec["university_name"],
                rec.get("doc_type", ""),
                len(ft),
                mode,
                llm_n,
            ))
        print("\n  モード別件数: {}".format(mode_counts))
        print("  推定 LLM 呼び出し合計: {} 回".format(total_llm_calls))
        print("\n[DRY-RUN] 実際の変更は行いません。")
        return

    success = 0
    failed = 0
    total_llm_calls = 0

    for i, rec in enumerate(all_records, 1):
        pdf_id = rec["id"]
        university_name = rec["university_name"]
        full_text = rec.get("full_text", "") or ""
        char_count = rec.get("char_count") or len(full_text)

        eu = rec.get("extracted_units") or {}
        if isinstance(eu, str):
            try:
                eu = json.loads(eu)
            except Exception:
                eu = {}
        covered = eu.get("covered_units", [])
        unit_name = covered[0].get("unit_name", "") if covered else ""

        print("[{}/{}] {} | {:,}字 | {} | {}".format(
            i, len(all_records), university_name, char_count,
            rec.get("doc_type", ""), rec.get("actual_year", ""),
        ))

        if not full_text or char_count < 100:
            print("  skipped: full_text empty or too short")
            failed += 1
            continue

        texts, mode = prepare_text_for_extraction(full_text, university_name)
        sizes = " + ".join("{:,}字".format(len(t)) for t in texts)
        print("  mode: {} | {} texts ({})".format(mode, len(texts), sizes))

        results = []
        for k, text in enumerate(texts):
            if mode == "SHORT":
                label = "全文"
            elif mode == "MEDIUM":
                label = "前半+後半抜粋"
            elif mode == "LONG":
                label = "キーワードページ抜粋"
            elif mode == "LONG_FALLBACK":
                label = "前後抜粋（キーワード未検出）"
            else:
                label = "キーワードページ抜粋（{}）".format("前半" if k == 0 else "後半")

            result = extract_one(llm_call, text, university_name, unit_name, label)
            total_llm_calls += 1
            time.sleep(SLEEP_BETWEEN_LLM)

            if result and result.get("exam_types"):
                n_et = len(result["exam_types"])
                print("  OK: {} exam_types extracted".format(n_et))
                results.append(result)
            elif result:
                gi = result.get("general_info", {})
                notes = gi.get("notes", "")[:80]
                print("  exam_types empty: {}".format(notes))
                results.append(result)
            else:
                print("  FAILED: JSON parse error")

        if mode == "LONG_SPLIT" and len(results) == 2:
            print("  merging 2 results...")
            final_result = merge_two_results(
                llm_call, results[0], results[1], university_name
            )
            total_llm_calls += 1
            time.sleep(SLEEP_BETWEEN_LLM)
        elif results:
            final_result = results[0]
        else:
            print("  no valid result -> saving empty")
            client.table("crawled_pdfs").update({
                "structured_data": {
                    "exam_types": [],
                    "general_info": {},
                    "_meta": {"note": "no extraction", "mode": mode},
                }
            }).eq("id", pdf_id).execute()
            failed += 1
            time.sleep(SLEEP_BETWEEN_PDFS)
            continue

        final_result["_meta"] = {
            "university_name": university_name,
            "unit_name": unit_name,
            "academic_year": rec.get("actual_year") or rec.get("academic_year", ""),
            "pdf_scope": rec.get("pdf_scope", ""),
            "pdf_url": rec.get("pdf_url", ""),
            "doc_type": rec.get("doc_type", ""),
            "mode": mode,
            "char_count": char_count,
        }

        n_exam_types = len(final_result.get("exam_types", []))
        print("  saved: {} exam_types".format(n_exam_types))

        client.table("crawled_pdfs").update({
            "structured_data": final_result
        }).eq("id", pdf_id).execute()

        success += 1
        time.sleep(SLEEP_BETWEEN_PDFS)

    print("\n" + "=" * 70)
    print("Phase 4A.5 done")
    print("  success: {}".format(success))
    print("  failed:  {}".format(failed))
    print("  total LLM calls: {}".format(total_llm_calls))


if __name__ == "__main__":
    main()