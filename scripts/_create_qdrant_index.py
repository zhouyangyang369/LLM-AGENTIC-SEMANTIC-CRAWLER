# -*- coding: utf-8 -*-
import sys, os, requests, warnings
if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
from dotenv import load_dotenv
load_dotenv()

QDRANT_URL = os.getenv('QDRANT_URL')
QDRANT_API_KEY = os.getenv('QDRANT_API_KEY')
headers = {'api-key': QDRANT_API_KEY, 'Content-Type': 'application/json'}

# インデックスを作成するフィールド
fields = [
    ('university_name', 'keyword'),
    ('academic_year',   'keyword'),
    ('pdf_scope',       'keyword'),
    ('exam_types',      'keyword'),
    ('page_number',     'integer'),
]

for field_name, field_type in fields:
    url = QDRANT_URL.rstrip('/') + '/collections/pdf_chunks/index'
    payload = {
        'field_name': field_name,
        'field_schema': field_type,
    }
    resp = requests.put(url, headers=headers, json=payload, verify=False, timeout=30)
    print('{} ({}): {} - {}'.format(
        field_name, field_type,
        resp.status_code,
        resp.text[:100]
    ))
