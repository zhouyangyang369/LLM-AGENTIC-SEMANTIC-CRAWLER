# Phase 4：RAG データ準備・検索精度向上戦略

> **作成日**：2026-06  
> **前提**：Phase 3 で収集した PDF リンク群（university_name × unit_name × academic_year × pdf_scope のメタデータ付き）を RAG の高品質データ基盤として活用する。

---

## 目次

1. [現在のデータ資産](#1-現在のデータ資産)
2. [日本の募集要項 PDF の特殊性](#2-日本の募集要項-pdf-の特殊性)
3. [推奨 RAG アーキテクチャ（4 レイヤー）](#3-推奨-rag-アーキテクチャ4-レイヤー)
4. [Phase 4A：PDF 全文構造化](#4-phase-4apdf-全文構造化)
5. [Phase 4B：Contextual Chunking](#5-phase-4bcontextual-chunking)
6. [Phase 4C：Vector Store 構築](#6-phase-4cvector-store-構築)
7. [Phase 4D：Retrieval Pipeline](#7-phase-4dretrieval-pipeline)
8. [施策の優先順位](#8-施策の優先順位)
9. [注意事項・制約](#9-注意事項制約)

---

## 1. 現在のデータ資産

Phase 3 完了時点で以下のデータが Supabase に蓄積される：

```
crawled_pdfs
├── pdf_url                    # 再ダウンロード可能な原本 URL
├── extracted_units (JSONB)    # LLM による構造化サマリー（アーカイブ）
├── pdf_scope                  # undergraduate / graduate / combined
├── academic_year              # 令和7年度 など
└── university_name            # 大学名

pdf_unit_coverage              # PDF ↔ unit 多対多リレーション
└── match_confidence           # high / medium / low

university_units               # Ground Truth（文科省 Excel 由来）
└── university_name × unit_type × unit_name × sub_unit_name
```

**Phase 3 の最大の強み**：各 PDF に `大学名 × 学部名 × 年度 × スコープ` の 4 軸メタデータが付与済み。  
これは汎用 RAG システムにはない**ドメイン特化フィルタリング基盤**となる。

---

## 2. 日本の募集要項 PDF の特殊性

### 2-1. 情報構造が階層的

```
募集要項（PDF）
├── 第1章 募集学部・学科・定員
├── 第2章 入学者選抜の方針
├── 第3章 出願資格・出願手続
│   ├── 3.1 一般選抜
│   ├── 3.2 学校推薦型選抜
│   └── 3.3 総合型選抜
├── 第4章 試験科目・配点
│   ├── 工学部 機械工学科 ...
│   └── 理学部 物理学科 ...
└── 第5章 合格発表・入学手続
```

単純な「見出しで切る」chunk 戦略では、「第4章 工学部の配点」が**どの入試方式のものか**という上位コンテキストが失われる。

### 2-2. 表格が主要な情報キャリア

配点・日程・出願期間はほぼ表格形式。  
`pdfplumber` の `extract_text()` のみでは**セル結合（rowspan）で情報が欠落**する：

```
【PDF 原本】
┌─────────┬──────────────┬──────┐
│ 学部名   │ 学科名        │ 定員 │
├─────────┼──────────────┼──────┤
│ 工学部   │ 機械工学科    │  80 │
│ (結合)   │ 電気電子工学科 │  80 │
└─────────┴──────────────┴──────┘

【extract_text() 結果】
工学部 機械工学科 80
電気電子工学科 80   ← 学部名が消えた
```

### 2-3. 同一質問に複数 PDF が関連

「東北大学の工学部の出願期間は？」  
→ 一般選抜要項 PDF + 総合型選抜要項 PDF + 学校推薦型選抜要項 PDF の 3 件が関連

### 2-4. LLM が読んでいるのは截断後テキストのみ

現状の `extracted_units.notes` も `covered_units` も、**MAX_TEXT_CHARS（現在 30,000字）で切り捨て後のテキスト**から生成されている。  
「〇〇の記載なし」という否定的判断は、後半部分を読んでいないため過信できない。

---

## 3. 推奨 RAG アーキテクチャ（4 レイヤー）

```
ユーザー質問
    │
    ▼
┌─────────────────────────────────┐
│ Layer 1: メタデータフィルタリング  │  ← Phase 3 の成果を直接活用
│  university × unit × year × scope│
└────────────────┬────────────────┘
                 │ 候補 PDF を 3〜5 件に絞り込み
                 ▼
┌─────────────────────────────────┐
│ Layer 2: Hybrid Search           │
│  BM25（固有名詞）                 │
│  + Vector Search（意味的類似）    │
│  → RRF で統合 → Top 20           │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ Layer 3: Reranking               │
│  CrossEncoder で Top 20 → Top 5  │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ Layer 4: LLM 回答生成            │
│  with citation（出典明示）        │
└─────────────────────────────────┘
```

---

## 4. Phase 4A：PDF 全文構造化

### 4-1. テキスト抽出の改善

現状の `extract_text()` に加え `extract_tables()` を併用：

```python
def extract_page_content(page) -> str:
    text = page.extract_text() or ""
    
    # 表格を Markdown テーブル形式で追加
    tables = page.extract_tables()
    for table in tables:
        for row in table:
            cells = [str(c).strip() for c in row if c]
            if cells:
                text += " | ".join(cells) + "\n"
    
    return text
```

### 4-2. 見出し検出とセクション分割

```python
# 日本語大学文書に頻出する見出しパターン
HEADING_PATTERNS = [
    r"^第\d+章\s+.+",          # 第1章 〇〇
    r"^[\(（]\d+[\)）]\s+.+",  # (1) 〇〇
    r"^[■□●○◆◇▶]\s+.+",      # ■ 〇〇
    r"^\d+\.\d*\s+.+",         # 1.2 〇〇
]
```

### 4-3. スキャン PDF 対応

`extract_text()` が空文字（文字数 < 10）の場合はスキャン PDF と判定 → OCR（`pytesseract` + `pdf2image`）にフォールバック。  
※ 現状のログでも `文字数=6` のような極端なケースが観測済み。表格問題よりも実害が大きい。

---

## 5. Phase 4B：Contextual Chunking

### 5-1. Chunk サイズの考え方

| chunk サイズ | 特性 |
|-------------|------|
| 小（200〜300字） | 検索精度高・回答に文脈不足 |
| **中（500〜1000字）← 推奨** | バランス良好 |
| 大（2000字〜） | 文脈豊富・ノイズ混入増 |

日本語募集要項の場合、1セクション（例：「出願期間」）は 200〜500字程度が多い。  
→ **500〜800字を目安**に、見出し境界を優先して切る。

### 5-2. Contextual Chunking（Anthropic 推奨手法）

各 chunk に「この chunk が属する文書全体の文脈」を LLM で付加する：

```python
CONTEXT_PROMPT = """
以下の文書の一部について、文書全体の文脈を踏まえた1〜2文の説明を日本語で生成してください。
この説明は検索インデックスに追加され、検索精度向上に使用されます。

【文書情報】
大学名: {university_name}
学部/研究科: {unit_name}
年度: {academic_year}
種別: {pdf_scope}

【文書全体の要約（先頭2000字）】
{doc_summary}

【対象 chunk】
{chunk_text}

【出力】context のみ（JSON 不要）:
"""
```

**付加前後の比較**：

```
# Before（chunk 単体）
"出願期間：2025年1月15日〜1月19日（消印有効）"

# After（context 付き）
"[東北大学 工学部 令和7年度 一般選抜前期日程 出願手続セクション]
 出願期間：2025年1月15日〜1月19日（消印有効）"
```

→ 「東北大学の出願はいつ？」という質問に対し、context なしでは単なる日付情報だが、context ありでは正しい chunk が上位に来る。

### 5-3. Chunk メタデータの設計

各 chunk に以下のメタデータを付与し、フィルタリングに活用：

```python
chunk_metadata = {
    # Phase 3 由来（フィルタリングキー）
    "university_name": "東北大学",
    "unit_name": "工学部",
    "unit_type": "学部",
    "academic_year": "令和7年度",
    "pdf_scope": "undergraduate",
    
    # Chunking 由来（検索補助）
    "section_path": "第3章出願手続 > 3.1一般選抜 > 出願期間",
    "page_number": 12,
    "pdf_url": "https://...",
    
    # 入試方式タグ（LLM 付与）
    "exam_type": ["一般選抜", "前期日程"],  # or 総合型・推薦型
}
```

### 5-4. 表格の扱い

```markdown
<!-- Markdown テーブル形式で保持 -->
| 学部 | 学科 | 入試方式 | 科目 | 配点 |
|------|------|---------|------|------|
| 工学部 | 機械工学科 | 一般前期 | 数学 | 200 |
| 工学部 | 機械工学科 | 一般前期 | 英語 | 200 |
```

表格1つを1 chunk として扱い、前後のテキスト見出しをコンテキストとして先頭に付加する。

---

## 6. Phase 4C：Vector Store 構築

### 6-1. Embedding モデルの選択

| モデル | 言語対応 | 次元 | 備考 |
|--------|---------|------|------|
| `multilingual-e5-large` | 多言語（日本語強）| 1024 | **推奨・無料** |
| `text-embedding-3-large` | 多言語 | 3072 | OpenAI 有料・高精度 |
| `cl-nagoya/sup-simcse-ja-large` | 日本語特化 | 1024 | 日本語のみならこれも検討 |

日本語固有名詞（学部名・大学名）の精度を考慮すると `multilingual-e5-large` が最もコスパが高い。

### 6-2. BM25 インデックス（日本語キーワード検索用）

```python
# 日本語形態素解析（sudachi 推奨、MeCab でも可）
import sudachipy

def tokenize_ja(text: str) -> list[str]:
    tokenizer = sudachipy.Dictionary().create()
    return [m.dictionary_form() for m in tokenizer.tokenize(text)]

# BM25 インデックス構築
from rank_bm25 import BM25Okapi
corpus_tokens = [tokenize_ja(chunk.text) for chunk in chunks]
bm25 = BM25Okapi(corpus_tokens)
```

### 6-3. ストレージ選択

| オプション | 特徴 |
|-----------|------|
| **Supabase pgvector** | 既存 DB と統合、メタデータフィルタが SQL で書ける、**推奨** |
| Pinecone | スケーラブル、ただし別サービス追加コスト |
| Chroma（ローカル） | 開発・検証用 |

Supabase pgvector を推奨する理由：Phase 3 で構築済みの `university_units` / `crawled_pdfs` テーブルと JOIN できるため、メタデータフィルタが SQL レベルで完結する。

---

## 7. Phase 4D：Retrieval Pipeline

### 7-1. クエリ処理（Query Understanding）

```python
def parse_query(user_query: str) -> dict:
    """LLM でクエリから検索条件を抽出"""
    # 例: 「北海道大学医学部の推薦入試の出願資格は？」
    # →  {
    #       "university": "北海道大学",
    #       "unit": "医学部",
    #       "exam_type": "学校推薦型選抜",
    #       "topic": "出願資格"
    #    }
```

### 7-2. メタデータフィルタリング（Layer 1）

```python
filters = {
    "university_name": parsed["university"],   # 必須
    "unit_name": parsed.get("unit"),           # 任意
    "academic_year": "令和7年度",              # 最新年度固定
    "pdf_scope": infer_scope(parsed),          # undergraduate/graduate
}
# → 候補 PDF を 3〜5 件に絞り込み
```

### 7-3. Hybrid Search（Layer 2）

```python
# BM25 スコア（固有名詞・専門用語に強い）
bm25_scores = bm25.get_scores(tokenize_ja(query))

# Vector スコア（意味的類似に強い）
query_embedding = embed(query)
vector_scores = cosine_similarity(query_embedding, chunk_embeddings)

# Reciprocal Rank Fusion（RRF）で統合
def rrf(bm25_ranks, vector_ranks, k=60):
    scores = {}
    for rank, idx in enumerate(bm25_ranks):
        scores[idx] = scores.get(idx, 0) + 1 / (k + rank)
    for rank, idx in enumerate(vector_ranks):
        scores[idx] = scores.get(idx, 0) + 1 / (k + rank)
    return sorted(scores, key=scores.get, reverse=True)

top20 = rrf(bm25_top20, vector_top20)
```

### 7-4. Reranking（Layer 3）

```python
from sentence_transformers import CrossEncoder

reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-12-v2")
scores = reranker.predict([(query, chunk.text) for chunk in top20])
top5 = [top20[i] for i in np.argsort(scores)[-5:]]
```

### 7-5. LLM 回答生成（Layer 4）with Citation

```python
ANSWER_PROMPT = """
以下の参考資料を基に、質問に日本語で回答してください。
回答の末尾に必ず出典を記載してください。

【質問】{query}

【参考資料】
{context_chunks}

【回答形式】
回答本文

【出典】
- {大学名} {学部名} 令和7年度募集要項 p.{page} ({pdf_url})
"""
```

---

## 8. 施策の優先順位

| 優先度 | 施策 | 効果 | コスト |
|--------|------|------|--------|
| ⭐⭐⭐ | **メタデータフィルタリング** | Phase 3 資産を直接活用。検索候補を 1/100 に絞り込む | 低（実装済み資産の再利用）|
| ⭐⭐⭐ | **Contextual Chunking** | chunk 単体では失われる文脈を LLM で補完。最高コスパ | 中（LLM API コスト）|
| ⭐⭐ | **表格の Markdown 保持** | 日本の募集要項の主要情報キャリアへの対応 | 低（pdfplumber 改修のみ）|
| ⭐⭐ | **Hybrid Search（BM25 + Vector）** | 学部名・入試方式名などの固有名詞対応 | 中（BM25 インデックス構築）|
| ⭐ | **Reranking** | 精度の最後の一押し | 中（CrossEncoder モデル）|
| ⭐ | **スキャン PDF OCR 対応** | 文字数=0 のケース救済 | 高（pytesseract + pdf2image）|

---

## 9. 注意事項・制約

### notes フィールドの信頼性

`crawled_pdfs.extracted_units.notes` は **截断後テキスト（MAX_TEXT_CHARS 以内）のみ**から生成されている。  
「〇〇の記載なし」という否定的判断は、PDF 後半を読んでいないため**補助的参考情報**として扱うこと。

### Contextual Chunking のコスト試算

| 規模 | chunk 数（概算）| LLM API コスト（概算）|
|------|----------------|----------------------|
| 国立大学のみ | ~50,000 chunk | ~$10〜20 |
| 全大学（国公私立） | ~500,000 chunk | ~$100〜200 |

コスト削減策：context 生成は各 PDF につき1回のみ（全 chunk 共通の context summary を生成して使い回す）。

### Ground Truth の精度限界

文科省 Excel に旧学部名が残存するケース（例：室蘭工業大学の「工学部」→ 現実は「理工学部」）が存在する。  
RAG の回答品質向上のため、Phase 3 完了後に `extracted_units.notes` を一括レビューし、改制・合併情報を別テーブルに管理することを推奨。

### 年度管理

募集要項は毎年更新される。Vector Store 構築時に `academic_year` をメタデータとして必ず付与し、古い年度のデータが上位に来ないよう検索時のフィルタまたはスコア減衰を実装すること。