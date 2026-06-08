"""
配置区 — 所有可调参数集中在此，运行前只需填好 API Keys。

使用方式：
  export PORTKEY_API_KEY="pk-..."
  export PORTKEY_VIRTUAL_KEY_GEMINI="..."   # Gemini 2.5 Flash
  export PORTKEY_VIRTUAL_KEY_CLAUDE="..."   # Claude Sonnet (可选，用于元数据抽取)
  export TAVILY_API_KEY="tvly-..."
"""

import os
from pathlib import Path

# ── API Keys (从环境变量读取，也可直接填字符串) ──────────────────────────
PORTKEY_API_KEY: str         = os.getenv("PORTKEY_API_KEY", "mnXWDHL9j0ntMnBPhfY6Fd9D8pP/")
TAVILY_API_KEY: str          = os.getenv("TAVILY_API_KEY", "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")

# ── LLM バックエンド切り替え ─────────────────────────────────────────
# "ollama"   → ローカル Ollama（自宅・オフライン環境）
# "portkey"  → 会社の Portkey ゲートウェイ（Gemini / Claude）
LLM_BACKEND: str = "portkey"   # ← ここを切り替えるだけ

# ── Ollama 設定（ローカル） ────────────────────────────────────────────
OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
OLLAMA_PRIMARY_MODEL: str = "qwen2.5:7b"  # qwen3 hangs on Ollama 0.23.2/M2 Pro; update Ollama to use qwen3

# ── Portkey 設定（会社） ──────────────────────────────────────────────
# 会社で使う場合: LLM_BACKEND = "portkey" に変えるだけ
PORTKEY_PRIMARY_MODEL: str = "@openai-eastus2/gpt-5.5"
PORTKEY_EXTRACT_MODEL: str = "@anthropic-eastus2/claude-opus-4-8"

# ── 実行時に使われるモデル（バックエンドに応じて自動選択）─────────────
PRIMARY_MODEL: str = OLLAMA_PRIMARY_MODEL if LLM_BACKEND == "ollama" else PORTKEY_PRIMARY_MODEL
EXTRACT_MODEL: str = OLLAMA_PRIMARY_MODEL if LLM_BACKEND == "ollama" else PORTKEY_EXTRACT_MODEL

# ── 路径 ────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
EXCEL_DIR  = BASE_DIR.parent / "university_excel"
OUTPUT_DIR = BASE_DIR / "results"
CACHE_DIR  = BASE_DIR / "results" / "_page_cache"
OUTPUT_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

# Excel 文件路径
OFFICIAL_EXCEL = {
    "national": EXCEL_DIR / "国立大学.xlsx",
    # 可扩展：public / private / etc.
}
SITEMAP_EXCEL = EXCEL_DIR / "university_sidemap_url.xlsx"

# Sitemap Excel 的 sheet 名 → 学校类型映射
SITEMAP_SHEETS = {
    "National university": "national",
    "Public university":   "public",
    "Private university":  "private",
}

# ── 爬取参数 ────────────────────────────────────────────────────────────
REQUEST_TIMEOUT: int   = 20        # HTTP 超时（秒）
MAX_SITEMAP_URLS: int  = 3000      # 单个 sitemap 最多处理 URL 数
LLM_FILTER_BATCH: int  = 200       # 每次喂给 LLM 的 URL 数量上限
MAX_SUBSITE_DEPTH: int = 1         # 子站递归层数（0=不递归）
MAX_PAGES_PER_SCHOOL: int = 80     # 每所学校最多抓取页面数（防失控）
MAX_AUDIT_ROUNDS: int  = 2         # LLM 完备性自检最大轮次

# ── 速率控制 ────────────────────────────────────────────────────────────
TAVILY_SLEEP: float = 1.2          # Tavily 调用间隔（秒）
LLM_SLEEP: float    = 0.3          # LLM 调用间隔（秒）
SCHOOL_SLEEP: float = 2.0          # 每所学校之间间隔（秒）
MAX_RETRY: int      = 3
RETRY_BACKOFF: float = 2.0

# ── 批量运行控制 ────────────────────────────────────────────────────────
LIMIT: int | None = None           # None=全部；数字=只跑前N所
TARGET_TYPES: list[str] = ["national"]  # 本次跑哪些类型的学校

# ── PDF 除外パターン（リンクテキストがこれらを含む場合は募集要項外と判断）────
PDF_EXCLUDE_TEXT_PATTERNS: list[str] = [
    "返還請求書", "郵送用紙", "返金申請", "書式", "様式", "申請書",
    "領収書", "納付書", "振込", "口座",
]

# ── 日语关键词池（用于 sitemap URL 预过滤，减少 LLM token 消耗）────────
ADMISSION_KEYWORDS_JA = [
    # 募集・要項
    "募集", "要項", "要项", "boshu", "youkou",
    # 入試・入学
    "入試", "入学", "nyushi", "nyugaku", "選抜", "senbatsu",
    # 出願
    "出願", "shutsugan", "application", "apply",
    # 学部・大学院
    "大学院", "daigakuin", "graduate", "学部", "gakubu", "undergraduate",
    # 課程種別
    "修士", "博士", "前期", "後期", "専門職", "master", "doctoral", "phd",
    # 研究科・学府
    "研究科", "kenkyuka", "学府", "gakufu", "学院",
    # 一般・推薦・外国人
    "一般", "推薦", "外国人", "留学生", "社会人",
    # 英語
    "admission", "admissions", "enrollment", "entry", "prospectus",
]
