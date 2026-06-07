# LLM Agentic Semantic Crawler — agentic_crawler

日本の大学（国立・公立・私立）の **募集要項 PDF** を LLM + LangGraph で自律的に収集するエージェント型クローラー。

## アーキテクチャ（5 層防護）

```
Layer 1  サイトマップ取得       sitemap.xml / sitemap index を再帰展開
Layer 2  LLM URL フィルタ       関連 URL を Gemini 2.5 Flash で選別
Layer 3  ナビ＆サブサイト発見   研究科別サイト（別ドメイン含む）を自動検出
Layer 4  Tavily フォールバック  サイトマップ不足・未収集研究科を補完
Layer 5  LLM 完備性審査        「漏れている研究科はないか？」を自己チェック
```

## ディレクトリ構成

```
agentic_crawler/
├── config.py                  # 全パラメータ・API Key
├── requirements.txt
├── llm/
│   ├── client.py              # Portkey 統一 LLM クライアント
│   └── prompts.py             # 日本語 prompt テンプレート
├── tools/
│   ├── university_loader.py   # Excel から大学情報を読み込み
│   ├── sitemap_parser.py      # sitemap.xml 再帰展開
│   ├── fetcher.py             # HTTP fetch → Markdown 変換（キャッシュ付）
│   └── tavily_search.py       # Tavily 検索ラッパー
├── agent/
│   ├── schemas.py             # Pydantic データモデル
│   ├── nodes.py               # LangGraph ノード（各処理ステップ）
│   └── graph.py               # ワークフロー編成
├── run_single.py              # 1 校デバッグ実行
├── run_batch.py               # 全校バッチ実行（断点続跑対応）
└── results/                   # 出力ディレクトリ（自動生成）
    ├── <学校名>/
    │   ├── <学校名>.json      # 収集結果
    │   └── <学校名>_trace.jsonl  # 意思決定ログ
    └── _summary.json
```

## セットアップ

```bash
# 仮想環境（プロジェクトルートの .venv を使用）
cd /path/to/LLM-AGENTIC-SEMANTIC-CRAWLER
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r agentic_crawler/requirements.txt
```

## API Key 設定

`agentic_crawler/config.py` の先頭または環境変数で設定:

```bash
export PORTKEY_API_KEY="pk-..."
export PORTKEY_VIRTUAL_KEY_GEMINI="..."   # Gemini 2.5 Flash
export PORTKEY_VIRTUAL_KEY_CLAUDE="..."   # Claude Sonnet (省略可)
export TAVILY_API_KEY="tvly-..."
```

または `config.py` 内に直接記入。

## 実行方法

```bash
cd agentic_crawler

# 1 校デバッグ（まずここから）
../.venv/bin/python run_single.py --school 東北大学
../.venv/bin/python run_single.py --school 東北大学 --verbose

# バッチ実行（全国立大学）
../.venv/bin/python run_batch.py

# 先頭 3 校のみ
../.venv/bin/python run_batch.py --limit 3

# 特定の学校を再実行（--force で上書き）
../.venv/bin/python run_batch.py --school 大阪大学 --force
```

## 出力 JSON スキーマ

```json
{
  "school": "東北大学",
  "official_url": "https://www.tohoku.ac.jp/",
  "pdfs": [
    {
      "url": "https://...",
      "text": "令和7年度 修士課程学生募集要項",
      "category": "修士",
      "year": "2025",
      "department": "理学研究科",
      "admission_type": "一般",
      "source_page": "https://..."
    }
  ],
  "found_departments": ["理学研究科", "工学研究科", ...],
  "missing_departments": [],
  "is_complete": true
}
```

## ベースライン

`fetch_jp_admission_pdfs.py`（プロジェクトルート）は Tavily + 規則ベース爬虫のベースラインとして保持。
