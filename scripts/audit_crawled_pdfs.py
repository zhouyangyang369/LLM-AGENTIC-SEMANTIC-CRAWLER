# -*- coding: utf-8 -*-
"""
审计 crawled_pdfs 表中的数据质量
- 查看各大学的 PDF 数量
- 分析 pdf_scope 分布
- 分析 academic_year 分布
- 检查 extracted_units 内容，识别非募集要项文档
- 统计 covered_units 为空的记录
"""
import sys
import os
import json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

# 国立大学（已爬取完成的41所）
CRAWLED_NATIONAL = [
    "北海道大学", "北海道教育大学", "室蘭工業大学", "小樽商科大学", "帯広畜産大学",
    "旭川医科大学", "北見工業大学", "弘前大学", "岩手大学", "東北大学",
    "宮城教育大学", "秋田大学", "山形大学", "福島大学", "茨城大学",
    "筑波大学", "筑波技術大学", "宇都宮大学", "群馬大学", "埼玉大学",
    "千葉大学", "東京大学", "東京医科歯科大学", "東京外国語大学", "東京学芸大学",
    "東京農工大学", "東京芸術大学", "東京工業大学", "東京海洋大学", "お茶の水女子大学",
    "電気通信大学", "一橋大学", "横浜国立大学", "上越教育大学", "新潟大学",
    "長岡技術科学大学", "富山大学", "金沢大学", "福井大学", "浜松医科大学",
    "滋賀医科大学",
]

def main():
    from src.db.supabase_client import get_supabase
    client = get_supabase()

    print("=" * 70)
    print("  crawled_pdfs データ品質監査レポート")
    print("=" * 70)

    # 全件取得（university_name と extracted_units のみ）
    print("\n[1] データ取得中...", file=sys.stderr)
    
    all_records = []
    page_size = 500
    offset = 0
    while True:
        r = client.table("crawled_pdfs")\
            .select("id,university_name,pdf_url,pdf_scope,academic_year,extracted_units")\
            .in_("university_name", CRAWLED_NATIONAL)\
            .range(offset, offset + page_size - 1)\
            .execute()
        if not r.data:
            break
        all_records.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size
        print(f"  取得済み: {offset} 件...", file=sys.stderr)

    print(f"\n対象レコード総数: {len(all_records)} 件\n")

    # ── 1. 大学別 PDF 数 ──────────────────────────────────────
    from collections import Counter, defaultdict
    univ_count = Counter(r["university_name"] for r in all_records)
    print("【1】大学別 PDF 数（上位20）")
    print(f"  {'大学名':<22} {'PDF数':>6}")
    print("  " + "-" * 30)
    for name, cnt in univ_count.most_common(20):
        flag = " ⚠️ 多すぎ" if cnt > 30 else ""
        print(f"  {name:<22} {cnt:>6}{flag}")

    # ── 2. academic_year 分布 ────────────────────────────────
    print("\n【2】academic_year 分布")
    year_count = Counter(r.get("academic_year", "NULL") for r in all_records)
    for year, cnt in sorted(year_count.items(), key=lambda x: -x[1]):
        pct = cnt / len(all_records) * 100
        flag = " ← 旧文書" if year and "令和7" not in year and "令和8" not in year and year != "NULL" else ""
        print(f"  {str(year):<20} {cnt:>5} 件  ({pct:.1f}%){flag}")

    # ── 3. pdf_scope 分布 ────────────────────────────────────
    print("\n【3】pdf_scope 分布")
    scope_count = Counter(r.get("pdf_scope", "NULL") for r in all_records)
    for scope, cnt in sorted(scope_count.items(), key=lambda x: -x[1]):
        pct = cnt / len(all_records) * 100
        print(f"  {str(scope):<20} {cnt:>5} 件  ({pct:.1f}%)")

    # ── 4. extracted_units の covered_units 分析 ─────────────
    print("\n【4】extracted_units 品質分析")
    empty_covered = 0
    has_covered = 0
    doc_type_counter = Counter()
    year_in_doc = Counter()
    suspicious_docs = []  # 非募集要項と疑われる文書

    # 非募集要項キーワード（notes や doc_type から検出）
    NON_ADMISSION_KEYWORDS = [
        "合格者", "合格発表", "成績", "入学手続", "入学式",
        "シラバス", "便覧", "学生便覧", "時間割",
        "紀要", "研究報告", "年次報告",
        "教員募集", "職員募集",
        "オープンキャンパス",
        "中期計画", "情報公開",
        "tax", "Tax", "policy", "Policy",  # 英文PDF混入
        "nhs", "NHS", "hospital",  # 誤爬取
    ]

    for rec in all_records:
        eu = rec.get("extracted_units")
        if not eu:
            empty_covered += 1
            continue

        if isinstance(eu, str):
            try:
                eu = json.loads(eu)
            except:
                empty_covered += 1
                continue

        covered = eu.get("covered_units", [])
        if not covered:
            empty_covered += 1
        else:
            has_covered += 1

        # doc_type 集計
        doc_type = eu.get("doc_type", eu.get("document_type", "unknown"))
        doc_type_counter[str(doc_type)] += 1

        # extracted_units 内の year
        inner_year = eu.get("academic_year", eu.get("year", ""))
        if inner_year:
            year_in_doc[str(inner_year)] += 1

        # 非募集要項キーワード検査
        notes = str(eu.get("notes", ""))
        univ_name_in_doc = str(eu.get("university_name", ""))
        full_text = notes + " " + univ_name_in_doc + " " + str(eu)

        for kw in NON_ADMISSION_KEYWORDS:
            if kw in full_text:
                suspicious_docs.append({
                    "id": rec["id"],
                    "university_name": rec["university_name"],
                    "pdf_url": rec.get("pdf_url", "")[:80],
                    "keyword": kw,
                    "covered_units_count": len(covered) if covered else 0,
                })
                break

    print(f"  covered_units あり:  {has_covered:>5} 件  ({has_covered/len(all_records)*100:.1f}%)")
    print(f"  covered_units なし:  {empty_covered:>5} 件  ({empty_covered/len(all_records)*100:.1f}%)")

    print(f"\n  doc_type 分布:")
    for dt, cnt in doc_type_counter.most_common(15):
        print(f"    {str(dt):<35} {cnt:>4} 件")

    print(f"\n  extracted_units 内 academic_year 分布:")
    for yr, cnt in sorted(year_in_doc.items(), key=lambda x: -x[1])[:15]:
        flag = " ← 旧文書要注意" if yr and "令和7" not in yr and "令和8" not in yr and "令和9" not in yr else ""
        print(f"    {str(yr):<25} {cnt:>4} 件{flag}")

    # ── 5. 疑わしい文書リスト ────────────────────────────────
    print(f"\n【5】非募集要項の疑い文書: {len(suspicious_docs)} 件")
    for doc in suspicious_docs[:30]:
        print(f"  [{doc['university_name']}] kw='{doc['keyword']}' covered={doc['covered_units_count']}")
        print(f"    URL: {doc['pdf_url']}")

    # ── 6. URL パターン分析（非 .ac.jp ドメイン）────────────
    print(f"\n【6】非 .ac.jp ドメインの PDF")
    non_ac_jp = [
        r for r in all_records
        if ".ac.jp" not in r.get("pdf_url", "")
    ]
    print(f"  非 .ac.jp: {len(non_ac_jp)} 件 / {len(all_records)} 件")
    url_domain_counter = Counter()
    for r in non_ac_jp:
        url = r.get("pdf_url", "")
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        url_domain_counter[domain] += 1
    for domain, cnt in url_domain_counter.most_common(20):
        print(f"  {domain:<45} {cnt:>4} 件")

    print("\n" + "=" * 70)
    print("監査完了")
    print("=" * 70)

if __name__ == "__main__":
    main()