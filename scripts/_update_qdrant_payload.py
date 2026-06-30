# -*- coding: utf-8 -*-
"""
Qdrant の payload.chunk_text を Supabase の完全な chunk_text で上書きする。
Embedding の再計算は不要。
"""
import sys, os, requests, warnings, time
if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dotenv import load_dotenv
load_dotenv()

QDRANT_URL        = os.getenv('QDRANT_URL')
QDRANT_API_KEY    = os.getenv('QDRANT_API_KEY')
QDRANT_COLLECTION = os.getenv('QDRANT_COLLECTION', 'pdf_chunks')

qdrant_headers = {
    'api-key': QDRANT_API_KEY,
    'Content-Type': 'application/json',
}

# Qdrant /scroll API で全 point の chunk_text（現在500字切り取り）を取得
# chunk_text が500字未満のものだけ Supabase REST API で直接取得して更新
print('Qdrant から全 points をスクロール取得中...')
all_points = []
next_offset = None
while True:
    payload_scroll = {
        'limit': 100,
        'with_payload': True,
        'with_vector': False,
    }
    if next_offset:
        payload_scroll['offset'] = next_offset
    resp = requests.post(
        QDRANT_URL.rstrip('/') + '/collections/' + QDRANT_COLLECTION + '/points/scroll',
        headers=qdrant_headers,
        json=payload_scroll,
        verify=False,
        timeout=30,
    )
    data = resp.json()
    points = data.get('result', {}).get('points', [])
    all_points.extend(points)
    next_offset = data.get('result', {}).get('next_page_offset')
    print('  取得: {:,} 件...'.format(len(all_points)))
    if not next_offset or not points:
        break

print('Qdrant 全 points: {:,} 件'.format(len(all_points)))

# chunk_text が500字以下（切り取られている可能性あり）の points を特定
short_points = [p for p in all_points if len(p.get('payload', {}).get('chunk_text', '')) >= 499]
print('500字以上の chunk_text を持つ points（再確認不要）: {:,} 件'.format(len(all_points) - len(short_points)))
print('500字ちょうどの points（切り取られた可能性あり）: {:,} 件'.format(len(short_points)))

# Supabase REST API で直接取得（httpx を使わず requests を使う）
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
supabase_headers = {
    'apikey': SUPABASE_KEY,
    'Authorization': 'Bearer ' + SUPABASE_KEY,
    'Content-Type': 'application/json',
}

print('\nSupabase REST API から chunk_text を取得中...')
all_chunks = []
offset = 0
while True:
    resp = requests.get(
        SUPABASE_URL.rstrip('/') + '/rest/v1/pdf_chunks',
        headers=supabase_headers,
        params={
            'select': 'id,chunk_text',
            'offset': offset,
            'limit': 1000,
        },
        verify=False,
        timeout=30,
    )
    data = resp.json()
    if not data:
        break
    all_chunks.extend(data)
    print('  取得: {:,} 件...'.format(len(all_chunks)))
    if len(data) < 1000:
        break
    offset += 1000

print('取得: {:,} 件'.format(len(all_chunks)))

# chunk_id -> chunk_text のマップを作成
chunk_map = {c['id']: c['chunk_text'] or '' for c in all_chunks}

# Qdrant payload を batch で更新（正しい形式）
# PUT /collections/{name}/points/payload
# { "payload": {"chunk_text": "..."}, "points": ["uuid1", "uuid2", ...] }
BATCH_SIZE = 100
total = len(all_chunks)
updated = 0
failed = 0

chunk_ids_list = list(chunk_map.keys())

for i in range(0, len(chunk_ids_list), BATCH_SIZE):
    batch_ids = chunk_ids_list[i:i+BATCH_SIZE]

    # 各 point を個別に更新
    for chunk_id in batch_ids:
        chunk_text = chunk_map.get(chunk_id, '')
        url = QDRANT_URL.rstrip('/') + '/collections/' + QDRANT_COLLECTION + '/points/payload'
        resp = requests.put(
            url,
            headers=qdrant_headers,
            json={
                'payload': {'chunk_text': chunk_text},
                'points': [chunk_id],
            },
            verify=False,
            timeout=30,
        )
        if resp.status_code == 200:
            updated += 1
        else:
            print('ERROR [{}]: {} - {}'.format(chunk_id[:8], resp.status_code, resp.text[:100]))
            failed += 1

    print('[{}/{}] 更新済み: {:,} 件'.format(
        min(i+BATCH_SIZE, len(chunk_ids_list)), len(chunk_ids_list), updated))
    time.sleep(0.2)

print('\n完了: {:,} 件更新 / {:,} 件失敗'.format(updated, failed))
