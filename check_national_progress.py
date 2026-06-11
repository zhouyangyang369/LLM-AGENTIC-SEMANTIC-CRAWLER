import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from src.db.supabase_client import get_supabase
from collections import defaultdict
sb = get_supabase()

# 1. 直接硬编码82所国立大学名单（文科省公式リスト）
national_names = set([
    '北海道大学','北海道教育大学','室蘭工業大学','小樽商科大学','帯広畜産大学',
    '旭川医科大学','北見工業大学','弘前大学','岩手大学','東北大学',
    '宮城教育大学','秋田大学','山形大学','福島大学','茨城大学',
    '筑波大学','筑波技術大学','宇都宮大学','群馬大学','埼玉大学',
    '千葉大学','東京大学','東京医科歯科大学','東京外国語大学','東京学芸大学',
    '東京農工大学','東京芸術大学','東京工業大学','東京海洋大学','お茶の水女子大学',
    '電気通信大学','一橋大学','横浜国立大学','新潟大学','長岡技術科学大学',
    '上越教育大学','富山大学','金沢大学','北陸先端科学技術大学院大学','福井大学',
    '山梨大学','信州大学','岐阜大学','静岡大学','浜松医科大学',
    '名古屋大学','愛知教育大学','名古屋工業大学','豊橋技術科学大学','三重大学',
    '滋賀大学','滋賀医科大学','京都大学','京都教育大学','京都工芸繊維大学',
    '大阪大学','大阪教育大学','兵庫教育大学','神戸大学','奈良教育大学',
    '奈良女子大学','和歌山大学','鳥取大学','島根大学','岡山大学',
    '広島大学','山口大学','徳島大学','鳴門教育大学','香川大学',
    '愛媛大学','高知大学','福岡教育大学','九州大学','九州工業大学',
    '佐賀大学','長崎大学','熊本大学','大分大学','宮崎大学',
    '鹿児島大学','鹿屋体育大学','琉球大学','政策研究大学院大学','総合研究大学院大学',
    'はこだて未来大学','北陸先端科学技術大学院大学',
])

# 2. 从university_units分页获取所有记录
all_units = []
offset = 0
while True:
    res = sb.table('university_units').select('university_name,last_found_year').range(offset, offset+999).execute()
    if not res.data:
        break
    all_units.extend(res.data)
    if len(res.data) < 1000:
        break
    offset += 1000

# 3. 按大学名聚合 total / covered
total_dict = defaultdict(int)
covered_dict = defaultdict(int)
for u in all_units:
    name = u['university_name']
    total_dict[name] += 1
    if u.get('last_found_year'):
        covered_dict[name] += 1

# 4. 分类
done_100 = []
in_progress = []
not_started = []
for name in national_names:
    t = total_dict.get(name, 0)
    c = covered_dict.get(name, 0)
    pct = (c / t * 100) if t > 0 else 0.0
    if t == 0 or c == 0:
        not_started.append((name, t, c, pct))
    elif pct >= 99.9:
        done_100.append((name, t, c, pct))
    else:
        in_progress.append((name, t, c, pct))

# 5. 输出
print(f"国立大学: {len(national_names)} 所  (unit総数取得: {len(all_units)} 件)")
print()
print(f"{'✅ 100%完了'} ({len(done_100)}所)")
print(f"  {'大学名':<22} {'total':>5} {'covered':>7} {'coverage':>9}")
print("  " + "-"*50)
for name, t, c, pct in sorted(done_100, key=lambda x: x[0]):
    print(f"  {name:<22} {t:>5}  {c:>7}  {pct:>8.1f}%")

print()
print(f"{'🔄 進行中'} ({len(in_progress)}所)")
print(f"  {'大学名':<22} {'total':>5} {'covered':>7} {'coverage':>9}")
print("  " + "-"*50)
for name, t, c, pct in sorted(in_progress, key=lambda x: -x[3]):
    bar = '█' * int(pct/5) + '░' * (20 - int(pct/5))
    print(f"  {name:<22} {t:>5}  {c:>7}  {pct:>8.1f}%  {bar}")

print()
print(f"{'⏳ 未開始(0%)'} ({len(not_started)}所)")
print(f"  {'大学名':<22} {'total':>5}")
print("  " + "-"*30)
for name, t, c, pct in sorted(not_started, key=lambda x: x[0]):
    print(f"  {name:<22} {t:>5}")

print()
print("=" * 60)
total_all = sum(total_dict.get(n, 0) for n in national_names)
covered_all = sum(covered_dict.get(n, 0) for n in national_names)
total_pct = (covered_all / total_all * 100) if total_all > 0 else 0
print(f"国立大学 合計: {total_all} unit 中 {covered_all} カバー済み")
print(f"国立大学 覆盖率: {total_pct:.1f}%")
print(f"  完了: {len(done_100)}所  進行中: {len(in_progress)}所  未開始: {len(not_started)}所")