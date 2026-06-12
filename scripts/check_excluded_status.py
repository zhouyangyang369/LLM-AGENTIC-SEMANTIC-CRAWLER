# -*- coding: utf-8 -*-
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dotenv import load_dotenv
load_dotenv()
from src.db.supabase_client import get_supabase
client = get_supabase()

# ibconsortium の除外状況確認
r = client.table('crawled_pdfs')\
    .select('id,university_name,pdf_url,is_excluded,exclusion_reason')\
    .like('pdf_url', '%ibconsortium%')\
    .execute()

print(f'ibconsortium 件数: {len(r.data)}')
for row in r.data:
    print(f'  is_excluded={row["is_excluded"]} | {row["pdf_url"][:70]}')
    print(f'  reason: {row["exclusion_reason"]}')

# full_text が NULL でない件数（Phase 4A 対象）
r2 = client.table('crawled_pdfs')\
    .select('id', count='exact')\
    .eq('is_excluded', False)\
    .is_('full_text', 'null')\
    .execute()
print(f'\nPhase 4A 残り対象件数: {r2.count}')
