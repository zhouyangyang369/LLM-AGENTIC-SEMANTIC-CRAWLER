# -*- coding: utf-8 -*-
"""
查询 Supabase，显示国立大学各所的覆盖率情况
"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

# 国立大学82所名单
NATIONAL_UNIVERSITIES = [
    "北海道大学", "北海道教育大学", "室蘭工業大学", "小樽商科大学", "帯広畜産大学",
    "旭川医科大学", "北見工業大学", "弘前大学", "岩手大学", "東北大学",
    "宮城教育大学", "秋田大学", "山形大学", "福島大学", "茨城大学",
    "筑波大学", "筑波技術大学", "宇都宮大学", "群馬大学", "埼玉大学",
    "千葉大学", "東京大学", "東京医科歯科大学", "東京外国語大学", "東京学芸大学",
    "東京農工大学", "東京芸術大学", "東京工業大学", "東京海洋大学", "お茶の水女子大学",
    "電気通信大学", "一橋大学", "横浜国立大学", "上越教育大学", "新潟大学",
    "長岡技術科学大学", "富山大学", "金沢大学", "北陸先端科学技術大学院大学", "福井大学",
    "山梨大学", "信州大学", "岐阜大学", "静岡大学", "浜松医科大学",
    "名古屋大学", "愛知教育大学", "名古屋工業大学", "豊橋技術科学大学", "三重大学",
    "滋賀大学", "滋賀医科大学", "京都大学", "京都教育大学", "京都工芸繊維大学",
    "大阪大学", "大阪教育大学", "兵庫教育大学", "神戸大学", "奈良教育大学",
    "奈良女子大学", "和歌山大学", "鳥取大学", "島根大学", "岡山大学",
    "広島大学", "山口大学", "徳島大学", "鳴門教育大学", "香川大学",
    "愛媛大学", "高知大学", "福岡教育大学", "九州大学", "九州工業大学",
    "佐賀大学", "長崎大学", "熊本大学", "大分大学", "宮崎大学",
    "鹿児島大学", "鹿屋体育大学", "琉球大学", "政策研究大学院大学", "総合研究大学院大学",
]

def main():
    from src.db.supabase_client import get_supabase
    client = get_supabase()

    # 1. crawled_pdfs から各大学の PDF 数を取得
    print("crawled_pdfs を照会中...", file=sys.stderr)
    r_pdfs = client.table("crawled_pdfs").select("university_name").execute()
    from collections import Counter
    pdf_count = Counter()
    if r_pdfs.data:
        for row in r_pdfs.data:
            name = row.get("university_name", "")
            if name:
                pdf_count[name] += 1

    # 2. pdf_unit_coverage から各大学のマッチ数を取得
    #    pdf_unit_coverage → crawled_pdfs → university_name の結合は重いので
    #    university_units の last_found_year で covered 数を数える
    #    ただし 10215 件あるので分割取得する
    print("university_units を照会中 (covered counts)...", file=sys.stderr)
    covered_count = Counter()
    total_count = Counter()

    page_size = 1000
    offset = 0
    while True:
        r = client.table("university_units")\
            .select("university_name,last_found_year")\
            .range(offset, offset + page_size - 1)\
            .execute()
        if not r.data:
            break
        for row in r.data:
            name = row.get("university_name", "")
            if not name:
                continue
            total_count[name] += 1
            if row.get("last_found_year"):
                covered_count[name] += 1
        if len(r.data) < page_size:
            break
        offset += page_size
        print(f"  取得済み: {offset} 件...", file=sys.stderr)

    print(f"university_units 合計: {sum(total_count.values())} 件", file=sys.stderr)

    # 3. 国立大学ごとに集計・表示
    print("\n" + "=" * 65)
    print(f"  国立大学 覆盖率一覧 (全 {len(NATIONAL_UNIVERSITIES)} 所)")
    print("=" * 65)
    print(f"{'大学名':<22} {'PDFs':>5} {'covered':>8} {'total':>6} {'coverage%':>10}")
    print("-" * 65)

    done = []
    partial = []
    not_done = []

    for name in NATIONAL_UNIVERSITIES:
        pdfs  = pdf_count.get(name, 0)
        cov   = covered_count.get(name, 0)
        tot   = total_count.get(name, 0)
        pct   = (cov / tot * 100.0) if tot > 0 else 0.0
        print(f"{name:<22} {pdfs:>5} {cov:>8} {tot:>6} {pct:>9.1f}%")
        if pdfs == 0:
            not_done.append(name)
        elif pct >= 100.0:
            done.append(name)
        else:
            partial.append(name)

    print("=" * 65)
    print(f"\n✅ 完了 (100%):     {len(done)} 所")
    print(f"🔄 部分完了:        {len(partial)} 所")
    print(f"❌ 未爬取 (0 PDF): {len(not_done)} 所")

    if not_done:
        print(f"\n--- 未爬取の国立大学 ({len(not_done)} 所) ---")
        for name in not_done:
            print(f"  {name}")

    if partial:
        print(f"\n--- 部分完了の国立大学 ({len(partial)} 所) ---")
        for name in sorted(partial, key=lambda n: (covered_count.get(n,0)/total_count.get(n,1))):
            cov = covered_count.get(name, 0)
            tot = total_count.get(name, 0)
            pct = cov / tot * 100.0 if tot > 0 else 0.0
            print(f"  {name}: {cov}/{tot} ({pct:.1f}%)")

if __name__ == "__main__":
    main()