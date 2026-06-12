# -*- coding: utf-8 -*-
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dotenv import load_dotenv
load_dotenv()

from src.db.supabase_client import get_supabase
client = get_supabase()

# jfm.go.jp の PDF を確認
r = client.table('crawled_pdfs')\
    .select('university_name,pdf_url,doc_type,actual_year,full_text')\
    .eq('is_excluded', False)\
    .not_.is_('full_text', 'null')\
    .limit(5)\
    .execute()

for row in r.data:
    ft = row.get('full_text') or ''
    print(f'大学: {row["university_name"]}')
    print(f'URL: {row["pdf_url"][:80]}')
    print(f'doc_type: {row["doc_type"]}')
    print(f'actual_year: {row["actual_year"]}')
    print(f'文字数: {len(ft)}')
    print(f'full_text冒頭300字:')
    print(ft[:300])
    print('-' * 60)
