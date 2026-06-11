import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from src.db.supabase_client import get_supabase
from collections import Counter, defaultdict
import json

sb = get_supabase()

print("=" * 60)
print("  Supabase データ品質チェック")
print("=" * 60)

# ── 1. crawled_pdfs 基本統計 ────────────────────────────────
print("\n【1. crawled_pdfs テーブル基本統計】")
pdfs = []
offset = 0
while True:
    res = sb.table('crawled_pdfs').select(
        'id,university_name,pdf_url,pdf_scope,academic_year,extracted_units,crawled_at'
    ).range(offset, offset+999).execute()
    if not res.data:
        break
    pdfs.extend(res.data)
    if len(res.data) < 1000:
        break
    offset += 1000

print(f"  総レコード数: {len(pdfs)} 件")

# scope分布
scope_counter = Counter(p['pdf_scope'] for p in pdfs)
print(f"\n  pdf_scope 分布:")
for k, v in scope_counter.most_common():
    print(f"    {k}: {v} 件 ({v/len(pdfs)*100:.1f}%)")

# academic_year分布
year_counter = Counter(p['academic_year'] for p in pdfs)
print(f"\n  academic_year 分布:")
for k, v in year_counter.most_common():
    print(f"    {k}: {v} 件 ({v/len(pdfs)*100:.1f}%)")

# 大学別PDF数
univ_pdf = Counter(p['university_name'] for p in pdfs)
print(f"\n  大学別PDF数 (上位10):")
for name, cnt in univ_pdf.most_common(10):
    print(f"    {name}: {cnt} 件")
print(f"\n  PDF数が最も少ない大学 (下位5):")
for name, cnt in univ_pdf.most_common()[:-6:-1]:
    print(f"    {name}: {cnt} 件")

# ── 2. extracted_units の品質チェック ───────────────────────
print("\n【2. extracted_units (JSONB) 品質チェック】")
null_extracted = sum(1 for p in pdfs if not p.get('extracted_units'))
print(f"  extracted_units が NULL: {null_extracted} 件")

# covered_units の数分布
covered_counts = []
notes_samples = []
low_confidence = 0
no_units = 0
year_from_extracted = Counter()

for p in pdfs:
    eu = p.get('extracted_units')
    if not eu:
        continue
    if isinstance(eu, str):
        try:
            eu = json.loads(eu)
        except:
            continue
    
    # academic_year from extracted_units
    year_from_extracted[eu.get('academic_year', 'なし')] += 1
    
    units = eu.get('covered_units', [])
    covered_counts.append(len(units))
    
    if len(units) == 0:
        no_units += 1
    
    # confidence チェック
    for u in units:
        if u.get('confidence') in ('low', 'medium'):
            low_confidence += 1
    
    # notes サンプル
    note = eu.get('notes', '')
    if note and len(notes_samples) < 5:
        notes_samples.append((p['university_name'], note[:80]))

if covered_counts:
    print(f"  covered_units 数の統計:")
    print(f"    平均: {sum(covered_counts)/len(covered_counts):.1f} unit/PDF")
    print(f"    最大: {max(covered_counts)} unit")
    print(f"    最小: {min(covered_counts)} unit")
    print(f"    0件 (空): {no_units} 件 ({no_units/len(covered_counts)*100:.1f}%)")
    print(f"  低confidence unit: {low_confidence} 件")

print(f"\n  extracted_units 内の academic_year 分布:")
for k, v in year_from_extracted.most_common():
    print(f"    {k}: {v} 件")

if notes_samples:
    print(f"\n  notes サンプル (5件):")
    for univ, note in notes_samples:
        print(f"    [{univ}] {note}")

# ── 3. pdf_unit_coverage テーブル ───────────────────────────
print("\n【3. pdf_unit_coverage テーブル】")
cov = []
offset = 0
while True:
    res = sb.table('pdf_unit_coverage').select(
        'match_confidence,match_method'
    ).range(offset, offset+999).execute()
    if not res.data:
        break
    cov.extend(res.data)
    if len(res.data) < 1000:
        break
    offset += 1000

print(f"  総レコード数: {len(cov)} 件")
conf_counter = Counter(c['match_confidence'] for c in cov)
method_counter = Counter(c['match_method'] for c in cov)
print(f"\n  match_confidence 分布:")
for k, v in conf_counter.most_common():
    print(f"    {k}: {v} 件 ({v/len(cov)*100:.1f}%)")
print(f"\n  match_method 分布:")
for k, v in method_counter.most_common():
    print(f"    {k}: {v} 件 ({v/len(cov)*100:.1f}%)")

# ── 4. university_units カバー状況 ──────────────────────────
print("\n【4. university_units カバー状況】")
units = []
offset = 0
while True:
    res = sb.table('university_units').select(
        'university_name,unit_type,last_found_year,last_crawled_at'
    ).range(offset, offset+999).execute()
    if not res.data:
        break
    units.extend(res.data)
    if len(res.data) < 1000:
        break
    offset += 1000

total = len(units)
covered = sum(1 for u in units if u.get('last_found_year'))
print(f"  総 unit 数: {total}")
print(f"  カバー済み: {covered} ({covered/total*100:.1f}%)")
print(f"  未カバー:   {total-covered} ({(total-covered)/total*100:.1f}%)")

unit_type_counter = Counter(u['unit_type'] for u in units if u.get('last_found_year'))
print(f"\n  カバー済み unit_type 内訳:")
for k, v in unit_type_counter.most_common():
    print(f"    {k}: {v} 件")

# ── 5. 問題点サマリー ────────────────────────────────────────
print("\n" + "=" * 60)
print("  ⚠️  品質問題サマリー")
print("=" * 60)
issues = []
if year_from_extracted.get('令和7年度', 0) == len([p for p in pdfs if p.get('extracted_units')]):
    issues.append("❌ academic_year が全件「令和7年度」にハードコード → Prompt修正済み(次回起動から有効)")
if no_units > 0:
    issues.append(f"⚠️  covered_units が空のPDF: {no_units}件 → LLM抽出失敗またはスキャンPDF")
if null_extracted > 0:
    issues.append(f"⚠️  extracted_units が NULL: {null_extracted}件 → 抽出処理未完了")
if low_confidence > 0:
    issues.append(f"ℹ️  低confidence unit: {low_confidence}件 → 要確認")

for issue in issues:
    print(f"  {issue}")
if not issues:
    print("  ✅ 重大な問題は検出されませんでした")