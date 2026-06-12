# -*- coding: utf-8 -*-
"""
查询 crawled_pdfs 表中已爬取的大学，与国立大学名单对比，
输出未爬取的国立大学列表，用于续跑。
"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

# 国立大学82所名单（文部科学省 令和6年度）
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

    print(f"国立大学名単: {len(NATIONAL_UNIVERSITIES)} 所", file=sys.stderr)
    print("crawled_pdfs を照会中...", file=sys.stderr)

    # crawled_pdfs から大学名一覧を取得（university_name カラムのみ）
    result = client.table("crawled_pdfs").select("university_name").execute()
    crawled_set = set()
    if result.data:
        for row in result.data:
            name = row.get("university_name", "")
            if name:
                crawled_set.add(name)

    print(f"crawled_pdfs に記録あり: {len(crawled_set)} 所", file=sys.stderr)

    # 分類
    already_crawled = []
    not_yet_crawled = []
    for name in NATIONAL_UNIVERSITIES:
        if name in crawled_set:
            already_crawled.append(name)
        else:
            not_yet_crawled.append(name)

    print(f"\n=== 国立大学 爬取状况 ===")
    print(f"  已爬取: {len(already_crawled)} 所")
    print(f"  未爬取: {len(not_yet_crawled)} 所")

    print(f"\n--- 已爬取的国立大学 ---")
    for name in already_crawled:
        print(f"  {name}")

    print(f"\n--- 未爬取的国立大学（需要续跑，共 {len(not_yet_crawled)} 所）---")
    for name in not_yet_crawled:
        print(name)


if __name__ == "__main__":
    main()