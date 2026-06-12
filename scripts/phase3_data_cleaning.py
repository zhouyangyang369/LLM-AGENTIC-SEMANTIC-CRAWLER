"""
Phase 3 完了後データクリーニングスクリプト
================================================
実行タイミング：国立大学の全量爬取が完了した後、Phase 4 着手前に実行する。

実行方法：
    python scripts/phase3_data_cleaning.py --dry-run   # 確認のみ（DB更新なし）
    python scripts/phase3_data_cleaning.py             # 実際にクリーニング実行

処理内容：
    Step 1: academic_year の修正
            crawled_pdfs.academic_year を extracted_units 内の実際の年度で上書き
    Step 2: 旧文書フラグ付与
            平成年度・令和5年度以前など明らかに古いPDFを is_outdated=true でマーク
            （※ is_outdated カラムが存在しない場合は academic_year で代替管理）
    Step 3: covered_units 空の PDF 調査・レポート
            LLM抽出結果が空の82件を一覧表示し、再処理候補を特定
    Step 4: PDF数異常大学の重複調査
            同一 university_name で PDF 数が多い大学を調査し、重複 URL を検出
    Step 5: 低 confidence マッチングのサマリー
            match_confidence が low のレコードを大学別に集計・表示
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
from src.db.supabase_client import get_supabase

# ── 定数 ────────────────────────────────────────────────────
# 対象年度：これより古い年度を「旧文書」とみなす
TARGET_YEARS = {"令和7年度", "令和8年度", "令和9年度"}  # 有効年度
OUTDATED_PATTERNS = [
    r"^平成",           # 平成XX年度
    r"^令和[1-6]年度$", # 令和1〜6年度
    r"^令和元年度$",
    r"^\d{4}年度$",     # 2023年度など（令和5以前相当）
]

# 「不明」「2026年度」など判断保留
UNCERTAIN_YEARS = {"不明", "2026年度", "2027年度"}


def is_outdated_year(year: str) -> bool:
    """年度文字列が旧文書かどうか判定"""
    if not year or year in TARGET_YEARS or year in UNCERTAIN_YEARS:
        return False
    for pat in OUTDATED_PATTERNS:
        if re.match(pat, year):
            return True
    # 令和6年度以前も旧文書
    m = re.match(r"^令和(\d+)年度", year)
    if m and int(m.group(1)) <= 6:
        return True
    return False


def fetch_all(table: str, columns: str) -> list[dict]:
    """ページネーションで全件取得"""
    sb = get_supabase()
    rows = []
    offset = 0
    while True:
        res = sb.table(table).select(columns).range(offset, offset + 999).execute()
        if not res.data:
            break
        rows.extend(res.data)
        if len(res.data) < 1000:
            break
        offset += 1000
    return rows


def step1_fix_academic_year(dry_run: bool) -> dict:
    """
    Step 1: academic_year の修正
    extracted_units->>'academic_year' の値で crawled_pdfs.academic_year を上書き
    """
    print("\n" + "=" * 60)
    print("Step 1: academic_year フィールドの修正")
    print("=" * 60)

    pdfs = fetch_all("crawled_pdfs", "id,university_name,academic_year,extracted_units")
    sb = get_supabase()

    stats = {"total": len(pdfs), "updated": 0, "already_correct": 0,
             "no_extracted": 0, "set_unknown": 0}
    update_log = []

    for pdf in pdfs:
        eu = pdf.get("extracted_units")
        if not eu:
            stats["no_extracted"] += 1
            continue

        if isinstance(eu, str):
            try:
                eu = json.loads(eu)
            except Exception:
                continue

        real_year = eu.get("academic_year", "").strip()
        current_year = pdf.get("academic_year", "")

        if not real_year:
            real_year = "不明"
            stats["set_unknown"] += 1

        if real_year == current_year:
            stats["already_correct"] += 1
            continue

        update_log.append({
            "id": pdf["id"],
            "university_name": pdf["university_name"],
            "old_year": current_year,
            "new_year": real_year,
        })
        stats["updated"] += 1

    print(f"  総レコード数    : {stats['total']}")
    print(f"  更新対象        : {stats['updated']}")
    print(f"  既に正しい      : {stats['already_correct']}")
    print(f"  extracted_units なし: {stats['no_extracted']}")

    # 更新後の年度分布プレビュー
    new_year_counter = Counter()
    for pdf in pdfs:
        eu = pdf.get("extracted_units")
        if eu and isinstance(eu, dict):
            new_year_counter[eu.get("academic_year", "不明")] += 1
        elif eu and isinstance(eu, str):
            try:
                new_year_counter[json.loads(eu).get("academic_year", "不明")] += 1
            except Exception:
                new_year_counter["パース失敗"] += 1
        else:
            new_year_counter["NULL"] += 1

    print(f"\n  修正後の academic_year 分布（予測）:")
    for year, cnt in new_year_counter.most_common(15):
        marker = "✅" if year in TARGET_YEARS else ("⚠️ 旧" if is_outdated_year(year) else "ℹ️")
        print(f"    {marker} {year}: {cnt}件")

    if dry_run:
        print(f"\n  [DRY RUN] {stats['updated']} 件を更新予定（実際には更新しない）")
        if update_log[:5]:
            print("  更新サンプル（最初の5件）:")
            for r in update_log[:5]:
                print(f"    [{r['university_name']}] {r['old_year']} → {r['new_year']}")
    else:
        print(f"\n  {stats['updated']} 件を更新中...")
        for i, r in enumerate(update_log):
            sb.table("crawled_pdfs").update(
                {"academic_year": r["new_year"]}
            ).eq("id", r["id"]).execute()
            if (i + 1) % 50 == 0:
                print(f"    {i + 1}/{len(update_log)} 件完了...")
        print(f"  ✅ academic_year 更新完了: {stats['updated']} 件")

    return stats


def step2_flag_outdated(dry_run: bool) -> dict:
    """
    Step 2: 旧文書の特定とレポート
    ※ is_outdated カラムは現状のスキーマに存在しないため、
       academic_year 修正後に旧文書リストを出力するのみ（Supabase コンソールで対応）
    """
    print("\n" + "=" * 60)
    print("Step 2: 旧文書の特定")
    print("=" * 60)

    pdfs = fetch_all("crawled_pdfs", "id,university_name,pdf_url,academic_year,extracted_units")

    outdated = []
    for pdf in pdfs:
        eu = pdf.get("extracted_units")
        real_year = ""
        if eu:
            if isinstance(eu, str):
                try:
                    eu = json.loads(eu)
                except Exception:
                    pass
            if isinstance(eu, dict):
                real_year = eu.get("academic_year", "")

        year_to_check = real_year or pdf.get("academic_year", "")
        if is_outdated_year(year_to_check):
            outdated.append({
                "id": pdf["id"],
                "university_name": pdf["university_name"],
                "year": year_to_check,
                "url": pdf["pdf_url"][:80],
            })

    print(f"  旧文書と判定されたPDF: {len(outdated)} 件")

    # 大学別集計
    univ_counter = Counter(r["university_name"] for r in outdated)
    print(f"\n  大学別内訳（件数が多い順）:")
    for name, cnt in univ_counter.most_common(10):
        print(f"    {name}: {cnt}件")

    # 年度別集計
    year_counter = Counter(r["year"] for r in outdated)
    print(f"\n  年度別内訳:")
    for year, cnt in year_counter.most_common():
        print(f"    {year}: {cnt}件")

    if not dry_run and outdated:
        print(f"\n  ⚠️  is_outdated カラムは現スキーマに存在しません。")
        print(f"  以下の SQL を Supabase コンソールで実行することを推奨します：")
        print(f"\n  -- カラム追加")
        print(f"  ALTER TABLE crawled_pdfs ADD COLUMN IF NOT EXISTS is_outdated BOOLEAN DEFAULT FALSE;")
        print(f"\n  -- 旧文書フラグ更新")
        print(f"  UPDATE crawled_pdfs")
        print(f"  SET is_outdated = TRUE")
        print(f"  WHERE academic_year ~ '^平成'")
        print(f"     OR (academic_year ~ '^令和(\\d+)年度' AND (regexp_match(academic_year, '(\\d+)'))[1]::int <= 6)")
        print(f"     OR academic_year = '令和元年度';")

    return {"outdated_count": len(outdated), "by_university": dict(univ_counter)}


def step3_empty_units_report() -> dict:
    """
    Step 3: covered_units が空の PDF 調査
    """
    print("\n" + "=" * 60)
    print("Step 3: covered_units が空の PDF 調査")
    print("=" * 60)

    pdfs = fetch_all("crawled_pdfs", "id,university_name,pdf_url,extracted_units,pdf_scope")

    empty_units = []
    for pdf in pdfs:
        eu = pdf.get("extracted_units")
        if not eu:
            empty_units.append({**pdf, "reason": "extracted_units=NULL"})
            continue
        if isinstance(eu, str):
            try:
                eu = json.loads(eu)
            except Exception:
                empty_units.append({**pdf, "reason": "JSON parse error"})
                continue
        units = eu.get("covered_units", [])
        if len(units) == 0:
            notes = eu.get("notes", "")[:100]
            empty_units.append({**pdf, "reason": f"covered_units=[] | notes: {notes}"})

    print(f"  covered_units が空のPDF: {len(empty_units)} 件")

    # 大学別集計
    univ_counter = Counter(r["university_name"] for r in empty_units)
    print(f"\n  大学別内訳（件数が多い順）:")
    for name, cnt in univ_counter.most_common(10):
        print(f"    {name}: {cnt}件")

    # scope別
    scope_counter = Counter(r.get("pdf_scope", "unknown") for r in empty_units)
    print(f"\n  pdf_scope 別:")
    for scope, cnt in scope_counter.most_common():
        print(f"    {scope}: {cnt}件")

    # サンプル表示
    print(f"\n  サンプル（最初の10件）:")
    for r in empty_units[:10]:
        print(f"    [{r['university_name']}] {r['pdf_url'][:60]}")
        print(f"      → {r.get('reason', '')[:80]}")

    return {"empty_count": len(empty_units), "by_university": dict(univ_counter)}


def step4_duplicate_pdf_check() -> dict:
    """
    Step 4: PDF 数が異常に多い大学の重複調査
    """
    print("\n" + "=" * 60)
    print("Step 4: 大学別 PDF 数・重複チェック")
    print("=" * 60)

    pdfs = fetch_all("crawled_pdfs", "id,university_name,pdf_url,academic_year")

    # 大学別PDF数
    univ_pdfs = defaultdict(list)
    for pdf in pdfs:
        univ_pdfs[pdf["university_name"]].append(pdf)

    print(f"  総PDF数: {len(pdfs)}")
    print(f"  大学数: {len(univ_pdfs)}")
    print(f"\n  PDF数ランキング（上位15大学）:")
    for name, plist in sorted(univ_pdfs.items(), key=lambda x: -len(x[1]))[:15]:
        print(f"    {name}: {len(plist)}件")

    # 重複 URL チェック（同一 university_name 内）
    dup_total = 0
    print(f"\n  重複URL（同一大学内で同じURLが複数存在）:")
    for name, plist in sorted(univ_pdfs.items(), key=lambda x: -len(x[1])):
        url_counter = Counter(p["pdf_url"] for p in plist)
        dups = {url: cnt for url, cnt in url_counter.items() if cnt > 1}
        if dups:
            dup_total += sum(cnt - 1 for cnt in dups.values())
            print(f"    [{name}] {len(dups)}件の重複URL:")
            for url, cnt in list(dups.items())[:3]:
                print(f"      ({cnt}回) {url[:70]}")

    if dup_total == 0:
        print("    ✅ 重複URLは検出されませんでした")
    else:
        print(f"\n  重複レコード総数: {dup_total} 件")

    return {"total_pdfs": len(pdfs), "by_university": {k: len(v) for k, v in univ_pdfs.items()}}


def step5_low_confidence_report() -> dict:
    """
    Step 5: 低 confidence マッチングのサマリー
    """
    print("\n" + "=" * 60)
    print("Step 5: 低 confidence マッチング調査")
    print("=" * 60)

    # pdf_unit_coverage と crawled_pdfs を結合
    cov = fetch_all("pdf_unit_coverage", "pdf_id,unit_id,match_confidence,match_method")
    pdfs = fetch_all("crawled_pdfs", "id,university_name")
    pdf_dict = {p["id"]: p["university_name"] for p in pdfs}

    conf_counter = Counter(c["match_confidence"] for c in cov)
    method_counter = Counter(c["match_method"] for c in cov)

    print(f"  総マッチング数: {len(cov)}")
    print(f"\n  confidence 分布:")
    for k, v in conf_counter.most_common():
        pct = v / len(cov) * 100
        bar = "█" * int(pct / 2)
        print(f"    {k:8}: {v:5}件 ({pct:5.1f}%) {bar}")

    print(f"\n  match_method 分布:")
    for k, v in method_counter.most_common():
        print(f"    {k:8}: {v:5}件 ({v/len(cov)*100:5.1f}%)")

    # low confidence の大学別集計
    low_by_univ = Counter()
    for c in cov:
        if c["match_confidence"] == "low":
            univ = pdf_dict.get(c["pdf_id"], "不明")
            low_by_univ[univ] += 1

    if low_by_univ:
        print(f"\n  low confidence の大学別内訳（上位10）:")
        for name, cnt in low_by_univ.most_common(10):
            print(f"    {name}: {cnt}件")

    return {
        "total": len(cov),
        "confidence": dict(conf_counter),
        "method": dict(method_counter),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Phase 3 完了後データクリーニング"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="確認のみ（DBの実際の更新は行わない）"
    )
    parser.add_argument(
        "--step", type=int, choices=[1, 2, 3, 4, 5],
        help="特定のステップのみ実行（省略時は全ステップ）"
    )
    args = parser.parse_args()

    mode = "🔍 DRY RUN モード（DB更新なし）" if args.dry_run else "✏️  実行モード（DB更新あり）"
    print(f"\n{'=' * 60}")
    print(f"  Phase 3 データクリーニング")
    print(f"  {mode}")
    print(f"{'=' * 60}")

    results = {}

    if args.step is None or args.step == 1:
        results["step1"] = step1_fix_academic_year(args.dry_run)

    if args.step is None or args.step == 2:
        results["step2"] = step2_flag_outdated(args.dry_run)

    if args.step is None or args.step == 3:
        results["step3"] = step3_empty_units_report()

    if args.step is None or args.step == 4:
        results["step4"] = step4_duplicate_pdf_check()

    if args.step is None or args.step == 5:
        results["step5"] = step5_low_confidence_report()

    # ── 総合サマリー ────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  クリーニング完了サマリー")
    print(f"{'=' * 60}")
    if "step1" in results:
        s = results["step1"]
        print(f"  Step 1 academic_year 修正: {s['updated']}件更新")
    if "step2" in results:
        s = results["step2"]
        print(f"  Step 2 旧文書:             {s['outdated_count']}件検出")
    if "step3" in results:
        s = results["step3"]
        print(f"  Step 3 空PDF:              {s['empty_count']}件")
    if "step4" in results:
        s = results["step4"]
        print(f"  Step 4 総PDF数:            {s['total_pdfs']}件")
    if "step5" in results:
        s = results["step5"]
        low = s["confidence"].get("low", 0)
        print(f"  Step 5 low confidence:     {low}件")

    if args.dry_run:
        print(f"\n  ⚠️  DRY RUN のため DB は更新されていません。")
        print(f"  実際に実行するには --dry-run を外してください：")
        print(f"  python scripts/phase3_data_cleaning.py")
    else:
        print(f"\n  ✅ クリーニング完了。Phase 4 へ進めます。")


if __name__ == "__main__":
    main()
