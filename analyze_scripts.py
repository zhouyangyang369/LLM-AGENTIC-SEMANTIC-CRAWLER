# -*- coding: utf-8 -*-
import os

scripts_dir = 'scripts'
files = sorted(os.listdir(scripts_dir))

# 分类
keep = []      # 保留
delete = []    # 删除

for f in files:
    if f == '__pycache__' or not f.endswith(('.py', '.ts', '.txt')):
        continue
    
    # 明确保留：核心pipeline脚本
    if f in [
        'phase4a_extract_fulltext.py',    # PDF全文提取
        'phase4a5_structured_extract.py', # 结构化抽取
        'phase4b_chunking.py',            # Chunking核心
        'phase4c_embedding.py',           # Embedding核心
        'phase4d_retrieval.py',           # RAG检索
        'phase4e_agent_v2.py',            # Agent最新版
        'phase4f_ragas_eval.py',          # 评估
        'rag_api_server.py',              # API服务器
        'phase4_step1_filter.py',         # 前置清洗
        'phase4_step2_fix_year.py',       # 年度修正
        'phase4_step3_classify.py',       # 分类标记
        'phase3_data_cleaning.py',        # 数据清洗
        '_create_qdrant_index.py',        # Qdrant索引创建
        '_update_qdrant_payload.py',      # Qdrant payload更新
    ]:
        keep.append(f)
    else:
        delete.append(f)

print('=== 保留 ({}) ==='.format(len(keep)))
for f in keep:
    print('  KEEP:', f)

print()
print('=== 删除 ({}) ==='.format(len(delete)))
for f in delete:
    print('  DEL: ', f)
