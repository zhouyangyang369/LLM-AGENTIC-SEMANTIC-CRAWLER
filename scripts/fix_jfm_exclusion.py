# -*- coding: utf-8 -*-
"""
jfm.go.jp の PDF を除外フラグ設定 + full_text をクリアする
"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dotenv import load_dotenv
load_dotenv()

from src.db.supabase_client import get_supabase
client = get_supabase()

# jfm.go.jp の PDF を検索
r = client.table('crawled_pdfs')\
    .select('id,university_name,pdf_url')\
    .like('pdf_url', '%jfm.go.jp%')\
    .execute()

if not r.data:
    print('jfm.go.jp の PDF は見つかりませんでした。')
else:
    print(f'対象: {len(r.data)} 件')
    for row in r.data:
        print(f'  [{row["university_name"]}] {row["pdf_url"][:80]}')

    # 除外フラグ設定 + full_text クリア
    ids = [row['id'] for row in r.data]
    for rid in ids:
        client.table('crawled_pdfs').update({
            'is_excluded': True,
            'exclusion_reason': '除外ドメイン: jfm.go.jp（地方財政研究助成・大学募集要項と無関係）',
            'full_text': None,
            'page_count': None,
            'char_count': None,
        }).eq('id', rid).execute()

    print(f'\n✅ {len(ids)} 件を除外設定 + full_text クリア完了')
