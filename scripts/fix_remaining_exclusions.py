# -*- coding: utf-8 -*-
"""
後から追加されたレコードで除外漏れになっているものを修正する
対象ドメイン: ibconsortium.mext.go.jp, jfm.go.jp など
"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dotenv import load_dotenv
load_dotenv()
from src.db.supabase_client import get_supabase
client = get_supabase()

# 除外すべきドメインリスト（Step1 と同じ）
EXCLUDED_DOMAINS = [
    'pref.saitama.lg.jp', 'pref.ibaraki.jp', 'tochigi-edu.ed.jp',
    'ibconsortium.mext.go.jp', 'dnc.ac.jp', 'janu.jp',
    'kouseikyoku.mhlw.go.jp', 'gender.go.jp', 'pref.akita.lg.jp',
    'pref.iwate.jp', 'cao.go.jp', 'bnw-inc.jp', 'shinken-ad.co.jp',
    'uploads.guim.co.uk', 'jfm.go.jp',
    'nhs.uk', 'cqc.org.uk', 'bbc.co.uk', 'bbc.com',
    'blackpoolteachinghospitals.nhs.uk', 'uk.a-hospital.com',
]

total_fixed = 0
for domain in EXCLUDED_DOMAINS:
    r = client.table('crawled_pdfs')\
        .select('id,university_name,pdf_url')\
        .eq('is_excluded', False)\
        .like('pdf_url', f'%{domain}%')\
        .execute()
    if r.data:
        print(f'{domain}: {len(r.data)} 件の除外漏れを発見')
        for row in r.data:
            print(f'  [{row["university_name"]}] {row["pdf_url"][:70]}')
            client.table('crawled_pdfs').update({
                'is_excluded': True,
                'exclusion_reason': f'除外ドメイン: {domain}',
                'full_text': None,
                'page_count': None,
                'char_count': None,
            }).eq('id', row['id']).execute()
            total_fixed += 1

if total_fixed == 0:
    print('除外漏れはありませんでした。')
else:
    print(f'\n✅ 合計 {total_fixed} 件の除外漏れを修正しました。')
