import sys, os
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from src.db.supabase_client import get_supabase

sb = get_supabase()

result = sb.table('university_units') \
    .select('unit_type, unit_name, sub_unit_name') \
    .eq('university_name', '室蘭工業大学') \
    .order('unit_type').order('unit_name').order('sub_unit_name') \
    .execute()

units = result.data

gakubu = [u for u in units if u['unit_type'] == '学部']
kenkyuka = [u for u in units if u['unit_type'] == '研究科']

print('=' * 50)
print('室蘭工業大学')
print('=' * 50)

print(f'\n【学部】({len(gakubu)} 件)')
for u in gakubu:
    line = f'  {u["unit_name"]}'
    if u.get('sub_unit_name'):
        line += f' / {u["sub_unit_name"]}'
    print(line)

print(f'\n【研究科】({len(kenkyuka)} 件)')
for u in kenkyuka:
    line = f'  {u["unit_name"]}'
    if u.get('sub_unit_name'):
        line += f' / {u["sub_unit_name"]}'
    print(line)

print(f'\n合計: {len(units)} 件')
