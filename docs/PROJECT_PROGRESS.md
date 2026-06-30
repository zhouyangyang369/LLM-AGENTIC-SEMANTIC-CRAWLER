# 日本高校入试募集要项爬取系统 —— 项目进度说明书

> **用途**：本文档面向 AI Agent（或新加入的开发者），用于在每次新 Session 开始时快速恢复项目上下文。  
> **维护原则**：每次有功能变更、调试结论、阶段推进时，请同步更新本文档对应章节。  
> **最后更新**：2026-07（新增第五阶段：教授信息 & 研究室URL爬取）

---

## 目录

1. [项目目标](#1-项目目标)
2. [整体技术栈](#2-整体技术栈)
3. [项目目录结构](#3-项目目录结构)
4. [数据库设计（Supabase）](#4-数据库设计supabase)
5. [第一阶段：强规则爬虫（Baseline）](#5-第一阶段强规则爬虫baseline)
6. [第二阶段：LLM Agentic 语义爬虫（ReAct）](#6-第二阶段llm-agentic-语义爬虫react)
7. [第三阶段：Ground Truth 驱动爬取（当前主线）](#7-第三阶段ground-truth-驱动爬取当前主线)
8. [第四阶段：RAG データ準備（計画中）](#8-第四阶段rag-データ準備計画中)
9. [第五阶段：教授信息 & 研究室URL爬取](#9-第五阶段教授信息--研究室url爬取)
10. [当前进度与待办事项](#10-当前进度与待办事项)
11. [已知问题与注意事项](#11-已知问题与注意事项)
12. [快速启动命令速查](#12-快速启动命令速查)
13. [更新日志](#13-更新日志)

---

## 1. 项目目标

**收集全日本高校（国立・公立・私立大学）的入学者选拔相关信息**，具体包括：

- 各大学 **学部**（本科各院系）的募集要项页面 URL 和 PDF 文件
- 各大学 **研究科**（研究生院）的募集要项页面 URL 和 PDF 文件
- 最终实现对全国所有大学、所有学部/研究科的**高覆盖率、结构化**数据采集

**数据来源基准**：日本文部科学省（文科省，MEXT）公开的全国高校一览 Excel 数据。

---

## 2. 整体技术栈

| 类别 | 技术/工具 |
|------|-----------|
| 语言 | Python 3.x |
| LLM 框架 | LangGraph + LangChain |
| LLM 后端 | Claude Sonnet 4（通过 JV Vortex OpenAI 兼容网关） / Portkey（旧） / Ollama（本地备选） |
| 搜索引擎 | DuckDuckGo Search（ddgs，免费无限制，替代 Tavily）|
| 数据库 | Supabase（PostgreSQL） |
| PDF 解析 | pdfplumber |
| 模糊匹配 | rapidfuzz |
| HTTP 抓取 | httpx / requests（带重试和 UA 轮换） |
| 网页转 Markdown | markdownify / html2text |
| 进度展示 | tqdm |
| Sitemap 解析 | 自研 `sitemap_parser.py` |

---

## 3. 项目目录结构

```
项目根目录/
├── run_phase3.py                  # 【第三阶段主入口】统一 CLI
│
├── agentic_crawler/               # 【第一/第二阶段代码包】
│   ├── run_single.py              # 第一阶段：单所大学调试运行
│   ├── run_batch.py               # 第一阶段：全量批量运行
│   ├── run_react.py               # 第二阶段：ReAct Agent 单校运行
│   ├── config.py                  # 全局配置（LLM 后端、关键词、路径等）
│   ├── agent/
│   │   ├── graph.py               # 第一阶段：LangGraph 固定流水线
│   │   ├── schemas.py             # AgentState / SchoolResult 数据结构
│   │   ├── nodes.py               # 第一阶段：流水线节点定义
│   │   └── react/
│   │       ├── graph.py           # 第二阶段：ReAct LangGraph 图
│   │       └── tools.py           # ReAct Agent 可调用工具集
│   ├── tools/
│   │   ├── fetcher.py             # 网页抓取 + Markdown 转换 + PDF 链接提取
│   │   ├── pdf_extractor.py       # PDF 标题强化（pdfplumber）
│   │   ├── sitemap_parser.py      # Sitemap 发现与解析
│   │   ├── tavily_search.py       # Tavily 搜索封装
│   │   └── university_loader.py   # 从 Excel 加载大学列表
│   └── llm/
│       ├── client.py              # LLM 客户端封装
│       └── prompts.py             # Prompt 模板
│
├── src/                           # 【第三阶段核心代码包】
│   ├── db/
│   │   ├── supabase_client.py     # Supabase 单例客户端
│   │   ├── operations.py          # 所有 DB 操作（upsert / query）
│   │   └── schema.sql             # 完整建表 SQL（含索引和视图）
│   ├── pipeline/
│   │   └── phase3/
│   │       ├── mext_downloader.py   # 从文科省网站下载 Excel（已完成）
│   │       ├── mext_excel_parser.py # 解析 Excel → 结构化记录（已完成）
│   │       ├── mext_importer.py     # 批量导入 university_units 表（已完成）
│   │       ├── crawl_graph.py       # LangGraph 爬取主图（待调试）
│   │       ├── batch_crawler.py     # 批量任务调度（待调试）
│   │       ├── unit_matcher.py      # exact/fuzzy/llm 三级匹配（待调试）
│   │       ├── pdf_downloader.py    # 带重试的 PDF 下载器（待调试）
│   │       ├── pdf_extractor.py     # pdfplumber + LLM 内容提取（待调试）
│   │       └── coverage_report.py  # 覆盖率报告（待调试）
│   └── utils/
│       └── logger.py              # 统一日志配置
│
├── university_excel/
│   ├── mext/                      # 【已入库】从文部科学省下载的原始 Excel
│   │   ├── 01国立大学一覧.xlsx
│   │   ├── 02公立大学一覧.xlsx
│   │   └── 03-1 ~ 03-8 私立大学一覧.xlsx（8个分册）
│   ├── university_sidemap_url.xlsx # 各大学 Sitemap URL 对照表
│   ├── 国立大学.xlsx
│   ├── 公立大学.xlsx
│   ├── 私立大学.xlsx
│   ├── 短期大学.xlsx
│   └── 高专.xlsx
│
├── docs/
│   ├── PROJECT_PROGRESS.md        # 【本文件】项目进度说明书
│   ├── phase3_architecture.md     # 第三阶段架构详细说明
│   ├── env_setup.md               # 环境配置说明
│   └── run_all_Univ-command.md    # 批量运行命令手册
│
├── requirements_phase3.txt        # 第三阶段依赖包
└── fetch_jp_admission_pdfs.py     # 早期独立脚本（已被后续阶段替代）
```

---

## 4. 数据库设计（Supabase）

数据库托管于 **Supabase**（PostgreSQL），建表 SQL 见 `src/db/schema.sql`。

### 三张核心表

#### `university_units`（Ground Truth 基准表）
来源于文科省 Excel 解析结果，是整个第三阶段爬取的静态目标基准。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `university_name` | TEXT | 大学名称 |
| `unit_type` | TEXT | `'学部'` 或 `'研究科'` |
| `unit_name` | TEXT | 学部名/研究科名 |
| `sub_unit_name` | TEXT | 学科名（学部）或専攻名（研究科） |
| `prefecture` | TEXT | 都道府县 |
| `last_found_year` | TEXT | 最后找到的年度，如 `'令和7年度'`，NULL 表示从未找到 |
| `last_crawled_at` | TIMESTAMPTZ | 最后爬取时间 |

#### `crawled_pdfs`（爬取 PDF 记录表）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `university_name` | TEXT | 所属大学 |
| `pdf_url` | TEXT | PDF 原始 URL |
| `content_hash` | TEXT | SHA-256，用于检测内容是否变化 |
| `pdf_scope` | TEXT | `'undergraduate'`/`'graduate'`/`'combined'` |
| `academic_year` | TEXT | 学年度，如 `'令和7年度'` |
| `extracted_units` | JSONB | LLM 提取的结构化结果（存档），含 `covered_units`、`notes`、`confidence` 等字段 |

#### `pdf_unit_coverage`（PDF-Unit 多对多关系表）

| 字段 | 类型 | 说明 |
|------|------|------|
| `pdf_id` | UUID | 关联 `crawled_pdfs.id` |
| `unit_id` | UUID | 关联 `university_units.id` |
| `match_confidence` | TEXT | `'high'`/`'medium'`/`'low'` |
| `match_method` | TEXT | `'exact'`/`'fuzzy'`/`'llm'` |

### 常用视图

- `v_university_coverage`：按大学汇总覆盖率（covered/total/coverage_pct）
- `v_uncovered_units`：所有未覆盖的 unit 清单，用于指导重试爬取

---

## 5. 第一阶段：强规则爬虫（Baseline）

### 状态：✅ 已完成（保留作对比基线）

### 核心文件
- `agentic_crawler/run_single.py` — 单所大学调试入口
- `agentic_crawler/run_batch.py` — 全量批量执行入口
- `agentic_crawler/agent/graph.py` — LangGraph 固定流水线图
- `agentic_crawler/agent/nodes.py` — 各流水线节点（解析 Sitemap → 筛选候选页 → 提取 PDF）

### 工作原理
1. 从 `university_excel/` 中读取大学列表（含 Sitemap URL）
2. 解析每所大学的 `sitemap.xml`，用**关键词规则**过滤出入试相关 URL
3. 逐页抓取过滤后的 URL，提取 PDF 链接
4. 结果以 JSON 格式保存到 `agentic_crawler/results/<大学名>/`

### 典型命令
```bash
cd agentic_crawler
python run_single.py --school 東京大学 --verbose
python run_batch.py --limit 5
python run_batch.py --types national public
```

### 阶段问题与局限
| 问题 | 说明 |
|------|------|
| **爬取速度慢** | 固定流水线逐节点串行处理，无灵活跳过机制 |
| **噪声多** | 强关键词规则会捕捉大量不相关页面 |
| **依赖 Sitemap** | 部分大学 Sitemap 质量差或不存在时效果显著下降 |
| **学部/研究科覆盖不全** | 没有预设目标列表，爬到哪算哪 |

---

## 6. 第二阶段：LLM Agentic 语义爬虫（ReAct）

### 状态：✅ 已完成（保留，可独立运行）

### 核心文件
- `agentic_crawler/run_react.py` — ReAct Agent 单校运行入口
- `agentic_crawler/agent/react/graph.py` — ReAct LangGraph 图定义
- `agentic_crawler/agent/react/tools.py` — Agent 可调用工具集

### 工作原理（四阶段流程）

```
[prepare] → [fetch_candidates] → [agent_node ⇄ tools_node] → [enrich_pdfs] → [finalize]
```

1. **prepare**（确定性阶段）：从 3 个来源收集候选入试 URL
   - Sitemap 关键词过滤（最多 80 条）
   - 首页导航链接抓取（Sitemap 命中 < 5 条时启用）
   - Tavily 搜索补充
2. **fetch_candidates**（确定性阶段）：对所有候选 URL 批量抓取，LLM 介入前完成最大化 PDF 提取（最多 20 页）
3. **agent_node ⇄ tools_node**（LLM 补完阶段）：LLM 以 ReAct 模式判断缺口，调用 `search_web` / `fetch_page` 补充，最多 15 步
4. **enrich_pdfs**：用 pdfplumber 提取 PDF 内实际标题，强化 text 字段

### PDF 相关性三级过滤
- **第一级（排除）**：中期计划、情报公开、シラバス、合格者成绩统计、过去问等管理类/结果类文档
- **第二级（正向文本）**：募集要项、選抜要項、出願要領 等关键词
- **第三级（正向 URL）**：`youkou`、`boshu`、`nyushi`、`admission` 等 URL 片段

### 典型命令
```bash
cd agentic_crawler
python run_react.py --school 室蘭工業大学
python run_react.py --school 北海道大学 --verbose
```

### 相比第一阶段的改进
| 方面 | 改进效果 |
|------|----------|
| **页面相关性** | 显著提高，LLM 语义判断替代纯关键词规则 |
| **速度** | 显著提升，确定性阶段批量预取，LLM 仅补完剩余缺口 |
| **异构网站适应性** | 更强，无需依赖规整 Sitemap |

### 阶段问题与局限（遗留硬伤）
| 问题 | 说明 |
|------|------|
| **学部/研究科覆盖不全** | 没有预设 ground truth，LLM 不知道该大学到底有哪些学部/研究科，无法判断是否遗漏 |
| **无法量化覆盖率** | 爬完之后无法精确得知哪些学部/研究科被覆盖、哪些遗漏 |
| **结果孤立** | 每次运行结果存为独立 JSON，无统一数据库支撑对比分析 |

---

## 7. 第三阶段：Ground Truth 驱动爬取（当前主线）

### 状态：🔄 进行中

### 核心思路
**先建立 Ground Truth，再有目的地爬取**。  
从文科省公开的全国高校一览 Excel 中提前解析出每所大学的所有学部/研究科，导入数据库作为静态基准（`university_units` 表），然后爬虫以此为目标，爬完后可精确计算覆盖率，知道哪些未覆盖、需要重试。

### 主入口
```
run_phase3.py  — 统一 CLI，子命令：download-excel / import-excel / crawl / report
```

### 各子模块完成情况

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 文科省 Excel 下载 | `mext_downloader.py` | ✅ 完成 | 自动从文科省网站下载最新高校一览 |
| Excel 解析 | `mext_excel_parser.py` | ✅ 完成 | 自适应表头识别，解析学部/研究科/専攻 |
| Ground Truth 入库 | `mext_importer.py` | ✅ 完成 | 批量 upsert 至 `university_units` 表 |
| 数据库 Schema | `schema.sql` | ✅ 完成 | 三表设计 + 索引 + 视图 |
| LangGraph 爬取图 | `crawl_graph.py` | ⚠️ 待调试 | 主爬取流程已设计，尚未联调 |
| 批量任务调度 | `batch_crawler.py` | ⚠️ 待调试 | 并发/顺序调度框架已完成，依赖 crawl_graph |
| Unit 匹配器 | `unit_matcher.py` | ⚠️ 待调试 | exact/fuzzy/llm 三级匹配逻辑已设计 |
| PDF 下载器 | `pdf_downloader.py` | ⚠️ 待调试 | 带重试和 UA 轮换，待联调 |
| PDF 内容提取 | `pdf_extractor.py` | ⚠️ 待调试 | pdfplumber + LLM，待联调 |
| 覆盖率报告 | `coverage_report.py` | ⚠️ 待调试 | 报告生成逻辑已完成，待端到端测试 |

### Ground Truth 数据源

`university_excel/mext/` 目录下已存有从文部科学省下载的原始 Excel 文件：

| 文件 | 内容 |
|------|------|
| `01国立大学一覧.xlsx` | 国立大学完整列表 |
| `02公立大学一覧.xlsx` | 公立大学完整列表 |
| `03-1 ~ 03-8 私立大学一覧.xlsx` | 私立大学（分8册） |

> ✅ **已完成**：上述所有 Excel 已通过 `import-excel` 子命令解析并入库 Supabase `university_units` 表。

### 第三阶段 LangGraph 爬取流程（当前实际）

```
load_units（从 DB 读取该大学所有 unit + Ground Truth）
    │
    ▼
search_pdfs（基于 Ground Truth 学部名生成精准查询 → Tavily 搜索 → 目标域名优先排序）
    │
    ▼
pick_url ◄──────────────────────────────────────────┐
    │                                                │
    ▼                                                │ should_continue=True
filter_url（规则过滤 + LLM 相关性判断）              │
    │ 通过                    │ 跳过                 │
    ▼                         ▼                      │
download              check_coverage                  │
    │ 成功      │ 失败         ▲                     │
    ▼           ▼             │                      │
extract    check_coverage     │                      │
（LLM）        ▲             │                      │
    │          │             │                      │
    ▼          │             │                      │
match_save ────┴─────────────┘──────────────────────┘
```

**停止条件**：
- 该大学所有 unit 均已匹配（全覆盖）→ 立即停止
- 没有更多候选 URL → 停止

### Unit 匹配策略（`unit_matcher.py`）

| 方法 | 触发条件 | 置信度 |
|------|----------|--------|
| `exact` | 字符串完全一致（标准化后） | high |
| `fuzzy` | rapidfuzz 相似度 ≥ 90 | high/medium |
| `llm` | rapidfuzz 相似度 75~90，不确定 | medium/low |
| 跳过 | 相似度 < 75 | — |

---

---

## 8. 第四阶段：RAG データ準備

### 状態：🔄 前置清洗進行中

### 前提条件
国立大学（41所）の爬取完了データを起点に着手。公立・私立は爬取完了後に順次追加（増分方式）。

### 核心思路
Phase 3 で付与した **`大学名 × 学部名 × 年度 × スコープ`** の 4 軸メタデータを RAG のフィルタリング基盤として活用し、汎用 RAG では実現できないドメイン特化の高精度検索を実現する。

### データベース設計変更（Phase 4 追加分）

#### `crawled_pdfs` 表に追加するフィールド
| 追加フィールド | 型 | 説明 |
|---|---|---|
| `doc_type` | TEXT | `募集要項` / `選抜要項` / `出願要領` / `合格発表` / `その他` |
| `actual_year` | TEXT | extracted_units 内部から読み取った実際の年度 |
| `is_scan_pdf` | BOOLEAN | スキャン版PDF（文字数<500）フラグ |
| `is_cleaned` | BOOLEAN | 前置清洗済みフラグ |
| `is_excluded` | BOOLEAN | 除外フラグ（非募集要項・無関係ドメイン等）|
| `exclusion_reason` | TEXT | 除外理由メモ |

#### 新規テーブル：`pdf_chunks`
| フィールド | 型 | 説明 |
|---|---|---|
| `id` | UUID | 主キー |
| `pdf_id` | UUID | crawled_pdfs.id への外部キー |
| `university_name` | TEXT | 大学名（検索フィルタ用） |
| `unit_name` | TEXT | 学部/研究科名（検索フィルタ用） |
| `academic_year` | TEXT | 年度（検索フィルタ用） |
| `pdf_scope` | TEXT | undergraduate/graduate/combined |
| `chunk_index` | INTEGER | PDF内のchunk番号 |
| `chunk_text` | TEXT | chunk本文（500~800字） |
| `chunk_context` | TEXT | LLMが付与したcontext説明（1~2文） |
| `section_path` | TEXT | 章節パス（例：第3章出願手続>3.1一般選抜）|
| `page_number` | INTEGER | ページ番号 |
| `exam_types` | TEXT[] | 入試方式タグ（一般/推薦/総合/社会人等）|
| `embedding` | vector(1024) | cohere.embed-multilingual-v3 ベクトル |
| `pdf_url` | TEXT | 出典URL |
| `created_at` | TIMESTAMPTZ | 作成日時 |

### 処理方式：分層アプローチ（コスト最適化）

```
PDF バイト列
    │
    ├─ 文字数 > 500字 ──→ 【Layer 1】pdfplumber extract_text + extract_tables
    │                              ↓ テキスト + Markdownテーブル結合
    │                       【Layer 2】見出し検出 → セクション分割
    │                              ↓ 500~800字/chunk
    │                       【Layer 3】LLM context付与（PDF単位で1回）
    │                              ↓
    │                       pdf_chunks テーブルに保存
    │
    └─ 文字数 < 500字 ──→ 【スキャンPDF】pymupdf → 画像変換
                                   → LLM ビジョン認識（Claude Sonnet）
                                   → テキスト復元後 Layer 2 へ
```

**コスト試算（国立41所・998件）**
| 処理 | 対象件数 | 推定コスト |
|------|---------|----------|
| pdfplumber テキスト抽出 | ~968件 | $0（無料）|
| LLM スキャンPDF認識 | ~30件 | ~$1~3 |
| LLM context付与 | ~968件×1回 | ~$5~15 |
| Embedding（cohere） | ~50,000 chunk | ~$0（Vortex経由）|

### 4 レイヤー構成

| レイヤー | 内容 | 優先度 |
|---------|------|--------|
| Layer 1 | **メタデータフィルタリング**（Phase 3 資産の直接活用） | ⭐⭐⭐ 最優先 |
| Layer 2 | **Hybrid Search**（BM25 + pgvector、RRF 統合） | ⭐⭐ |
| Layer 3 | **Reranking**（CrossEncoder） | ⭐ |
| Layer 4 | **LLM 回答生成**（出典明示付き） | — |

### 実装ロードマップ

```
【前置清洗 ✅完了】
  Step 1: scripts/phase4_step1_filter.py
    └── 54件に is_excluded=True（無関係ドメイン・非募集要項）
    └── 1313件に is_cleaned=True
    └── jfm.go.jp など後から発見されたドメインは
        scripts/fix_remaining_exclusions.py で随時追加除外

  Step 2: scripts/phase4_step2_fix_year.py
    └── 1320件の academic_year / actual_year を実際の年度に修正
    └── 年度分布：令和7年度545件・令和8年度417件・令和9年度104件 等

  Step 3: scripts/phase4_step3_classify.py
    └── 1328件に doc_type タグ付与
        （募集要項78%・選抜要項13%・その他4%・入学案内1%等）
    └── exam_types タグ付与（大学院・一般選抜・推薦・総合型等）
    └── is_scan_pdf フラグ設定（現時点で0件）

【Phase 4A 🔄実行中】scripts/phase4a_extract_fulltext.py
  └── pdfplumber extract_text + extract_tables（表格Markdown化）
  └── 全ページ対象（截断なし）
  └── スキャンPDF（文字数<500）→ LLM ビジョン認識
  └── 結果を crawled_pdfs.full_text / page_count / char_count に保存
  └── 対象: 1340件（除外済みを除く）

【Phase 4A.5 ✅実験完了（10大学）】scripts/phase4a5_structured_extract.py
  └── 実装方式：全文一括送信（FULLTEXT）モード
      当初のMap-Reduce（4,000字チャンク）方式を廃止。
      Claude Sonnet 4の200K context windowを活用し全文をそのまま送信。
      max_tokens=8192に設定し出力截断を防止。
  └── 実験対象：10所国立大学（山形・大阪・福島・横浜国立・名古屋工業・
      上越教育・旭川医科・北見工業・東京外国語・金沢）91件（有効84件）
  └── 実験結果：
      - 抽出成功：75件（82%）
      - 空/失敗：9件（募集要項でない文書が大半・正当スキップ）
      - 合計 exam_types：444件
  └── 旧截取方式との比較：exam_types 248件→444件（+79%）
      application_period等の日程情報充填率が大幅改善
  └── フィールド充填率：type 100% / target 97% / capacity 60%
  └── 結果を crawled_pdfs.structured_data（JSONB）に保存済み

【Phase 4B ✅実験完了（10大学）】scripts/phase4b_chunking.py
  └── 見出し境界でセクション分割（500~800字/chunk）
  └── 各chunkに structured_data の要約を context として付加
      chunk_text_with_context =
        "[大学名 学部名 年度 入試方式]"
        "[出願期間: XX〜XX | 試験日: XX | 定員: XX名]"
        "[LLM生成のセクション説明]"
        "[chunk本文]"
  └── pdf_chunks テーブルへ保存

【Phase 4C ✅実験完了（10大学）】scripts/phase4c_embedding.py
  └── cohere.embed-multilingual-v3（Vortex外部ゲートウェイ経由）
  └── API形式：input + input_type（OpenAI互換）
  └── 4,159件全件成功・0件失敗
  └── Qdrant Cloud pdf_chunksコレクション（1024次元 Cosine）に保存完了
  └── Supabase pdf_chunks.id = Qdrant point_id で紐付け

【Phase 4D 📋実装待ち】src/pipeline/phase4/retrieval_pipeline.py
  └── Query Understanding（LLMでクエリ→検索条件抽出）
  └── Qdrant payload フィルタ + ベクトル検索
  └── BM25 + RRF 統合（Hybrid Search）
  └── CrossEncoder Reranking
  └── LLM 回答生成（出典URL・ページ番号付き）
```

### structured_data 抽出フィールド設計

```json
{
  "university_name": "北海道大学",
  "unit_name": "工学部",
  "academic_year": "令和7年度",
  "exam_types": [
    {
      "type": "一般選抜前期日程",
      "application_period": {"start": "2025-01-27", "end": "2025-02-05", "notes": "消印有効"},
      "exam_date": "2025-02-25",
      "result_date": "2025-03-10",
      "enrollment_deadline": "2025-03-18",
      "capacity": 80,
      "exam_subjects": [{"subject": "数学", "score": 200}, {"subject": "英語", "score": 200}],
      "application_requirements": ["調査書", "志願票", "写真"],
      "qualification": "高等学校卒業または同等以上"
    },
    {
      "type": "学校推薦型選抜",
      "application_period": {"start": "2024-11-01", "end": "2024-11-07"},
      "exam_date": "2024-11-20",
      ...
    }
  ]
}
```

> 詳細設計：`docs/phase4_rag_strategy.md` 参照

---

---

## 9. 第五阶段：教授信息 & 研究室URL爬取

### 状态：🔄 进行中

### 背景与目标
在完成募集要项爬取（Phase 1~4）的基础上，项目新增了**旧帝大7校教员信息采集**功能，面向前端网站展示教授简介、研究方向及研究室主页链接。  
目标大学：**東京大学・京都大学・大阪大学・名古屋大学・東北大学・北海道大学・九州大学**（共7所）

### 核心文件

| 文件 | 说明 |
|------|------|
| `scripts/crawl_professors.py` | **主爬虫**：Selenium + researchmap.jp 爬取教员基本信息 |
| `scripts/crawl_lab_urls.py` | **研究室URL爬虫**：DuckDuckGo搜索大学官网域名的研究室页面 |
| `add_lab_url_and_crawl.py` | 早期版本（duckduckgo_search库），功能与crawl_lab_urls.py相同 |
| `check_lab_url_quality.py` | 数据质量报告：覆盖率、域名分析、重复URL、问题PDF链接 |
| `check_lab_url_sample.py` | 快速抽样查看已爬取的lab_url内容 |
| `crawl_professors.log` | 教员爬取运行日志 |
| `crawl_lab_urls.log` | 研究室URL爬取日志（当前为空，尚未运行） |

### 数据库表：`professor`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID/INT | 主键 |
| `researchmap_id` | TEXT | researchmap.jp 用户ID（唯一约束，用于 upsert） |
| `university_name` | TEXT | 所属大学名（7校之一） |
| `name_ja` | TEXT | 日文氏名 |
| `name_en` | TEXT | 英文氏名 |
| `name_kana` | TEXT | 假名氏名 |
| `affiliation` | TEXT | 所属（研究科+职位全文，最多400字） |
| `kenkyuka_name` | TEXT | 研究科名（从affiliation正则提取） |
| `senkou_name` | TEXT | 专攻名（从affiliation正则提取） |
| `position` | TEXT | 职位（教授/准教授/助教/講師等） |
| `research_fields` | TEXT[] | 研究分野列表（最多10个） |
| `keywords` | TEXT[] | 研究关键词列表 |
| `researchmap_url` | TEXT | researchmap.jp 个人页面URL |
| `lab_url` | TEXT | 研究室官网URL（DuckDuckGo搜索获取） |
| `profile_updated` | TEXT | researchmap.jp 上的更新日期 |
| `updated_at` | TIMESTAMPTZ | 本系统最后更新时间 |

### 爬取流程

#### Step 1：教员基本信息爬取（`crawl_professors.py`）

```
[初始化 Selenium ChromeDriver]
    │
    ▼
[collect_urls] — researchmap.jp/researchers?q=<大学名>&page=N
    │  逐页抓取，直到新URL=0为止（最多200页）
    │  过滤条件：URL格式匹配 + 排除/researchers/new_accounts/auth
    ▼
[逐URL访问个人页] — driver.get(researchmap_url)
    │  提取：氏名(h1) / カナ・英語名 / 所属・職位 / 研究キーワード / 研究分野 / 更新日
    │  403响应 → 等待30秒后重试（最多3次）
    │  Session失效 → 重建driver（每200件触发一次）
    ▼
[批量 upsert 至 Supabase professor 表]
    │  BATCH_SIZE = 30
    │  on_conflict=researchmap_id（幂等性保证）
    ▼
[下一所大学]
```

**职位过滤白名单**：教授・准教授・助教・講師・特任教授・特任准教授・特任講師・特任助教・客員教授・客員准教授・招へい教授・招へい准教授

#### Step 2：研究室URL补全（`crawl_lab_urls.py`）

```
[读取 professor 表中 lab_url=NULL 的记录]
    │
    ▼
[对每位教员执行 DuckDuckGo 搜索]
    │  查询模板：
    │    ①「<大学名> <教員名> 研究室」
    │    ②「<大学名> <教員名> lab」
    │    ③「<大学名> <教員名> <研究分野> 研究室」
    │  scoring规则：
    │    +10 大学公式ドメイン（u-tokyo.ac.jp等）
    │    +5  URL含 lab/labo/laboratory/research/group/prof/faculty
    │    +3  .ac.jp
    │    +2  URL深度≥4层
    │  阈值：score≥10 → 立即采用；score≥3 → 备选
    ▼
[PATCH professor 表，写入 lab_url]
    │  每2秒间隔（DDG限流对策）
    │  每50件输出一次进度统计
```

**除外URL规则**：researchmap.jp / kaken.nii.ac.jp / jglobal.jst.go.jp / scholar.google / ci.nii.ac.jp / wikipedia / linkedin / twitter / facebook / top-researchers.com / lab-search.com / 就职信息站点（mynavi/rikunabi/benesse等）

### 当前进度（截至2026-07）

| 步骤 | 状态 | 数据量 | 说明 |
|------|------|--------|------|
| **Step 1：教员信息爬取** | 🔄 进行中 | ~190件已入库 | researchmap.jp 爬取中，东京大学（2250条URL）、京都大学（1107条URL）已完成URL收集，正在逐页抓取详情 |
| **Step 2：研究室URL补全** | 📋 待启动 | 0件 | crawl_lab_urls.log为空，尚未运行 |

**各大学爬取进度**（researchmap.jp 搜索「総件数」仅为平台显示的注册研究者总数，受分页限制实际可收集URL远少于此数）：

| 大学 | 平台総件数 | 实际收集URL数 | 已入库件数 | 状态 |
|------|-----------|--------------|-----------|------|
| 東京大学 | 7,860件 | 2,250件（48页） | 合计约190件（含京都，多次中断重启累计） | 🔄 详情爬取中 |
| 京都大学 | 8,028件 | 1,107件（24页） | ↑ 同上（无法单独区分） | 🔄 详情爬取中 |
| 大阪大学 | 未知 | 未收集 | 0件 | 📋 待开始 |
| 名古屋大学 | 未知 | 未收集 | 0件 | 📋 待开始 |
| 東北大学 | 未知 | 未收集 | 0件 | 📋 待开始 |
| 北海道大学 | 未知 | 未收集 | 0件 | 📋 待开始 |
| 九州大学 | 未知 | 未收集 | 0件 | 📋 待开始 |

### 已知问题与注意事项

| 问题 | 说明 |
|------|------|
| **researchmap.jp 访问集中提示** | 频繁访问时返回「アクセスが集中しております」页面，当前脚本会将该响应当作正常数据写入（`name_ja`为错误文本），需后续清洗 |
| **ChromeDriver Session 崩溃** | 长时间运行后出现 `invalid session id` 错误，每200件会主动重建driver，但崩溃后需手动重启脚本 |
| **渲染超时** | 偶发 `Timed out receiving message from renderer`，重试3次后跳过，不影响整体进度 |
| **ERR_CONNECTION_RESET** | 网络不稳定时触发，重试3次跳过 |
| **lab_url 品质问题** | 部分URL可能指向PDF文件（非研究室主页）、京大KDB个人档案页等，需后续过滤 |
| **重复URL问题** | 同一URL可能分配给多位教授（同研究室），属正常现象但需关注 |
| **「このサイトにアクセスできません」** | researchmap个人页面返回此文本时，被当作有效数据写入，需清洗 |

### 快速命令

```bash
# 爬取教员基本信息（旧帝大7校）
python scripts/crawl_professors.py

# 补全研究室URL（lab_url=NULL的记录）
python scripts/crawl_lab_urls.py

# 查看数据质量报告
python check_lab_url_quality.py

# 抽样查看lab_url结果
python check_lab_url_sample.py
```

---

## 10. 当前进度与待办事项

### ✅ 已完成
- [x] 第一阶段强规则爬虫（run_single + run_batch）
- [x] 第二阶段 ReAct LLM 语义爬虫（run_react）
- [x] 第三阶段数据库 Schema 设计与创建（Supabase）
- [x] 文科省 Excel 数据下载模块
- [x] Excel 解析器（自适应表头）
- [x] Ground Truth 数据入库（`university_units` 表已有全国大学学部/研究科数据，约10,215条）
- [x] **`crawl_graph.py` 调试完成**：全部8个节点通过，端到端流程跑通
- [x] **`unit_matcher.py` 验证**：exact/fuzzy 匹配逻辑正确，精度高
- [x] **依赖环境修复**：`.venv` 中安装 pdfplumber、rapidfuzz、supabase、python-dotenv
- [x] **LLM 客户端重构**：支持 openai_compat / portkey / ollama 三后端切换，修复缩进 bug，修复模型参数兼容性
- [x] **LLM 后端切换至 JV Vortex**：`@bedrock-uswest2/us.anthropic.claude-sonnet-4-6`，通过 OpenAI 兼容接口调用
- [x] **`run_phase3.py crawl` CLI 验证**：室蘭工業大学、北見工業大学两所大学完整跑通
- [x] **`run_phase3.py report` CLI 验证**：覆盖率报告正常输出，818所大学列表全部正确
- [x] **Phase3 精准搜索改进**：基于 Ground Truth 学部/研究科名生成精准 Tavily 查询，目标域名自动推断并优先排序
- [x] **Phase3 相关性过滤改进（借鉴 Phase2）**：新增 `node_filter_url` 节点，规则过滤第三方平台/无关文档，LLM 判断模糊 URL
- [x] **效果验证**：北見工業大学覆盖率 71%→100%，处理 PDF 数 11→6，耗时 452s→140s（节省69%）
- [x] **国立大学全量爬取启动**：82所国立大学批量爬取任务已在后台运行（2026-06-11）
- [x] **pdf_extractor.py MAX_TEXT_CHARS 调整**：12,000字 → 30,000字，提升超大 PDF 覆盖率
- [x] **Phase 4 RAG 战略文档**：`docs/phase4_rag_strategy.md` 设计完成

### 🔄 进行中 / 待做
- [ ] **国立大学全量爬取**：44所未爬取国立大学后台运行中（DuckDuckGo替代Tavily，已验证正常）
- [ ] **全量批量爬取（公立・私立）**：国立完成后继续推进公立（99所）、私立（600+所）
- [ ] **数据质量问题**：文科省 Excel 含历史旧学部名（如工学部/理工学部并存），需清理或在 unit_matcher 中加入别名映射

#### Phase 4 前置清洗（✅ 全部完成）
- [x] **Step 1 过滤**：54件除外（無関係ドメイン・合格発表等）、1313件 is_cleaned=True
- [x] **Step 2 年度修正**：1320件の actual_year / academic_year を実際の年度に修正
- [x] **Step 3 分类标记**：1328件に doc_type / exam_types / is_scan_pdf タグ付与
- [x] **数据库变更**：`src/db/schema_phase4.sql` 実行済み（crawled_pdfs 9フィールド追加 + pdf_chunks 新規作成 + ビュー2件）
- [x] **Qdrant 設計確定**：ベクトルは Supabase pgvector でなく Qdrant Cloud に保存（chunk_id で紐付け）

#### Phase 4 主体实装
- [x] **Phase 4A スクリプト生成**：`scripts/phase4a_extract_fulltext.py`
- [ ] **Phase 4A 実行中**：🔄 バックグラウンド実行中（対象1340件、pdfplumber全文抽出 → crawled_pdfs.full_text）
- [x] **Phase 4A.5 実験完了**：`scripts/phase4a5_structured_extract.py` — FULLTEXTモード、10大学84件処理、444 exam_types抽出、crawled_pdfs.structured_data保存完了
- [x] **Phase 4B 実験完了**：`scripts/phase4b_chunking.py` — 見出し境界分割+強制截断、4,159 chunks生成（最大900字・平均660字）
- [x] **Phase 4C 実験完了**：`scripts/phase4c_embedding.py` — Cohere embed-multilingual-v3、Qdrant Cloudに4,159 points保存完了
- [x] **Phase 4D 実験完了**：`scripts/phase4d_retrieval.py` — RAG検索・回答生成、50問バッチテスト完了（平均score 0.8158、成功率100%）
- [ ] **Phase 4E 全国展開**：10大学→全国展開（Phase 4A〜4D を全大学に適用）
- [ ] **Phase 4C**：`scripts/phase4c_embedding.py` — cohere.embed-multilingual-v3 → Qdrant Cloud
- [ ] **Phase 4D**：`src/pipeline/phase4/retrieval_pipeline.py` — Qdrant検索 + BM25 + Rerank + LLM回答生成

### 🚧 已知难点
- 日本高校网站高度异构，部分大学无规整 Sitemap
- 部分大学募集要项 PDF 存放于子域名或第三方平台
- LLM Token 消耗需控制（`pdf_extractor.py` 已调整至 30,000 字符截断）
- 私立大学数量庞大（8 册 Excel），全量爬取耗时可能较长
- **文科省 Excel 数据质量**：部分大学存在新旧学部名并存问题（如室蘭工業大学：工学部→理工学部改制），导致 ground truth 不准确，覆盖率虚低
- **Tavily 搜索域名污染**：搜索结果会混入其他大学的 PDF，当前无域名过滤，需后续加入大学官网域名白名单
- **扫描 PDF（文字数=0）**：`extract_text()` 返回空字符串，现阶段直接跳过，Phase 4A 中需 OCR 对応
- **notes 字段可靠性**：`extracted_units.notes` 中的否定性判断（「〇〇の記載なし」）仅基于截断后文本，不代表 PDF 全文
- **academic_year 硬编码问题**：`crawled_pdfs.academic_year` 字段因 Prompt 模板硬编码全部写入「令和7年度」；`extracted_units` 内部实际读取年度分布显示约49%的PDF并非令和7年度（令和8年度297件、令和9年度67件、历史旧文档等）。Prompt 已修复（下次启动生效），已有数据需 Phase 3 完成后补跑清洗
- **历史旧文档混入**：爬取结果中含平成27年度（2015年）等历史文档，RAG 检索时会干扰结果，Phase 4 前需按年度过滤
- **部分大学 PDF 数量异常偏多**：茨城大学54件（正常5~15件），可能因各専攻独立PDF + 历史年度文档叠加，需调查
- **covered_units 空的 PDF**：82件（8.8%）LLM 提取结果为空，原因为扫描版 PDF 或非募集要項类文档
- **低 confidence 匹配**：pdf_unit_coverage 中 medium+low 合计占23.6%（922件），对 RAG 精度有潜在影响

---

## 11. 已知问题与注意事项

### 环境配置
- 需要在项目根目录创建 `.env` 文件，包含以下变量：
  ```
  SUPABASE_URL=https://your-project.supabase.co
  SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
  TAVILY_API_KEY=your-tavily-api-key
  OPENAI_API_KEY=your-openai-api-key
  LLM_MODEL=gpt-4o-mini
  ```
- 详细环境配置见 `docs/env_setup.md`

### 代码注意事项
| 注意点 | 说明 |
|--------|------|
| **阶段独立性** | 第一/二阶段代码在 `agentic_crawler/` 包内，第三阶段在 `src/` 包内，互不干扰 |
| **路径问题** | `agentic_crawler/` 内脚本需从该目录内执行（`cd agentic_crawler`），或使用 `sys.path.insert` |
| **Windows 编码** | 部分脚本有 `cp932` 日文字符编码处理，Windows 环境需注意；测试脚本需在开头加 `sys.stdout.reconfigure(encoding='utf-8')` |
| **反爬限制** | 批量爬取建议 `--max-workers 1~2`，避免触发高校服务器反爬 |
| **Excel 结构变化** | 文科省每年 Excel 列结构可能微调，`mext_excel_parser.py` 已有自适应表头识别 |
| **PDF 内容截断** | `pdf_extractor.py` 限制最大 30,000 字符（已从12,000调整），超长 PDF 自动截断送 LLM |
| **LLM 客户端** | `crawl_graph.py` 复用 `agentic_crawler/llm/client.py` 的 `llm_call`，LLM 后端切换在 `agentic_crawler/config.py` 中统一控制 |
| **Claude temperature** | `agentic_crawler/llm/client.py` 已修复：含 `claude-opus`/`o1`/`o3` 的模型自动跳过 temperature 参数 |
| **第三阶段 venv** | 第三阶段必须使用项目根目录的 `.venv`，已安装：pdfplumber、rapidfuzz、supabase、python-dotenv、tavily-python、beautifulsoup4 |
| **LLM 后端配置** | `agentic_crawler/config.py` 中 `LLM_BACKEND="openai_compat"` 为当前生效配置，使用 JV Vortex 网关（`https://ai-jv.vortex.sandisk.com/v1/`），模型为 `@bedrock-uswest2/us.anthropic.claude-sonnet-4-6` |
| **filter_url 节点** | 规则过滤优先（无LLM）：URL含 janu.jp/benesse/keinet/dnc.ac.jp/kobekyo.com 等直接跳过；含 boshu/youkou/senbatsu 等正向词直接保留；其余调用 LLM 判断 KEEP/SKIP |

### 数据库注意事项
- `university_units` 表使用 `(university_name, unit_type, unit_name, sub_unit_name)` 作为唯一约束，执行 upsert 时若已存在则更新
- `crawled_pdfs` 用 `content_hash`（SHA-256）去重，同一 URL 内容变化才插新行
- 建议在 Supabase 控制台定期查看 `v_uncovered_units` 视图了解爬取进展

---

## 12. 快速启动命令速查

### 第三阶段（当前主线）

```bash
# 安装依赖
pip install -r requirements_phase3.txt

# 1. 下载文科省 Excel（如需更新 Ground Truth）
python run_phase3.py download-excel

# 2. 导入 Ground Truth 到数据库（先 dry-run 检查）
python run_phase3.py import-excel --excel university_excel/mext/01国立大学一覧.xlsx --dry-run
python run_phase3.py import-excel --excel university_excel/mext/01国立大学一覧.xlsx

# 3. 爬取指定大学（调试用）
python run_phase3.py crawl --universities 北海道大学 東北大学

# 4. 批量爬取（先小批测试）
python run_phase3.py crawl --limit 10 --max-workers 1

# 5. 查看覆盖率报告
python run_phase3.py report
python run_phase3.py report --uncovered-only --output report.csv
```

### 第二阶段（ReAct，可独立使用）

```bash
cd agentic_crawler
python run_react.py --school 室蘭工業大学
python run_react.py --school 北海道大学 --verbose
```

### 第一阶段（Baseline，仅作对比参考）

```bash
cd agentic_crawler
python run_single.py --school 東京大学 --verbose
python run_batch.py --limit 5
python run_batch.py --types national public
```

---

## 13. 更新日志

| 日期 | 更新内容 |
|------|----------|
| 2025-07 | 创建本进度说明书；第三阶段 Ground Truth 入库（university_units）已完成；crawl_graph 等后续模块待调试 |
| 2025-07 | **crawl_graph 调试完成**：修复 LLM 客户端（改用 Portkey 统一封装）、修复 temperature 参数兼容性（Claude opus-4 不支持）、修复 bytes 存 State 内存问题、安装缺失依赖（pdfplumber/rapidfuzz/supabase）。端到端测试通过：室蘭工業大学 23 PDF 全部下载+提取+写库，exact 精确匹配，SHA-256 去重正常。遗留：文科省 Excel 含历史旧学部名（工学部/理工学部并存），属数据质量问题非代码 Bug。 |
| 2025-07 | **CLI 入口验证完成**：`run_phase3.py crawl` 和 `report` 子命令全部通过。室蘭工業大学(60%)、北見工業大学(71%)正常爬取。注意：全局参数（`--log-level`等）必须放在子命令名称之前。 |
| 2025-07 | **Phase3 重大改进**：①精准搜索——基于 Ground Truth 学部/研究科名生成精准查询，目标域名自动推断优先排序；②相关性过滤——新增 `node_filter_url` 节点，规则过滤 janu.jp/benesse/keinet/dnc.ac.jp 等无关域名，LLM 判断模糊URL；③LLM 后端切换至 JV Vortex（Claude Sonnet 4，OpenAI 兼容接口）并修复 llm_call 缩进 bug；验证结果：北見工業大学覆盖率 71%→100%，耗时节省 69%。 |
| 2026-06 | **国立大学全量爬取启动**：82所国立大学批量任务在后台运行，初步结果显示上越教育大学/北見工業大学/滋賀医科大学/電気通信大学/浜松医科大学等覆盖率100%，北海道大学38.5%（截断问题）。`MAX_TEXT_CHARS` 从12,000调整至30,000。确认 `extracted_units` 为LLM从截断后PDF文本中提取的结构化数据，`notes` 字段的否定性判断不可过度依赖。 |
| 2026-06 | **Phase 4 RAG 战略设计完成**：新增 `docs/phase4_rag_strategy.md`，明确4层RAG架构（元数据过滤→Hybrid Search→Rerank→LLM生成），确定 Contextual Chunking + Supabase pgvector + multilingual-e5-large 技术选型，优先级排序完成。PROJECT_PROGRESS.md 新增第四阶段章节，目录序号全部更新。 |
| 2026-06 | **数据质量检查完成（国立大学爬取进行中，930件）**：①`academic_year` 全件硬编码为「令和7年度」（实际约49%为其他年度）→ Prompt 已修复；②历史旧文档（平成27年度等）混入；③82件 covered_units 为空；④茨城大学 PDF 数异常（54件）；⑤低 confidence 匹配占23.6%。pdf_unit_coverage 3,907件中 exact 匹配97%、high confidence 76.4%，整体匹配质量良好。待办新增「Phase 3完了後データクリーニング」。 |
| 2026-06 | **Tavily → DuckDuckGo 切换完成**：Tavily月度配额耗尽，改用 ddgs 库（免费无限制）。新增非.jp域名过滤逻辑，测试验证北陸先端科学技術大学院大学100%覆盖。后台启动44所未爬取国立大学爬取任务。 |
| 2026-06 | **Phase 4 设计最终确定**：①数据库方案：crawled_pdfs表新增9个字段（含full_text/structured_data）+ 新建pdf_chunks表；②向量存储改用Qdrant Cloud（不用Supabase pgvector）；③处理方案：分层架构（pdfplumber全文提取 + LLM视觉扫描PDF），成本约$5~15；④前置清洗分3步；⑤新增Phase 4A.5（全文structured_data抽取）；⑥chunk策略确定：先全文抽取structured_data→再chunk→将structured_data附着每个chunk提升embedding语义质量。 |
| 2026-06 | **Phase 4 前置清洗全部完成**：Step1过滤54件无关文档（含jfm.go.jp等）；Step2修正1320件年度（令和7~9年度为主）；Step3为1328件打doc_type/exam_types标签（募集要項78%・選抜要項13%）。schema_phase4.sql已在Supabase执行，数据库变更完成。 |
| 2026-06 | **Phase 4A 开始执行**：PDF全文结构化抽取（pdfplumber text+extract_tables，全页面不截断）在后台运行，对象1340件，结果存入crawled_pdfs.full_text。**Phase 4A.5设计确定**：全文Map-Reduce结构化抽取策略（出願期間/試験科目/配点/定員/出願資格等）→ structured_data JSONB字段；chunk时将structured_data摘要附着到每个chunk的context中，显著提升RAG检索精度。 |
| 2026-07 | **Phase 4A.5 実験完了（10大学）**：当初設計のMap-Reduce方式からFULLTEXT（全文一括）方式に変更。Claude Sonnet 4の200K context windowを活用、max_tokens=8192で出力截断を防止。実験10国立大学91件（有効84件）処理、75件成功（82%）、合計444 exam_types抽出。旧截取方式比でexam_types+79%・日程情報充填率が大幅改善。9件の失敗はエネルギー報告・教員紹介等の非募集要項文書で正当スキップ。crawled_pdfs.structured_dataに保存完了。次フェーズ：Phase 4B Chunking実装開始。 |
| 2026-07 | **Phase 4B 実験完了（10大学）**：見出し正規表現パターンによるセクション分割＋強制字数截断（900字上限）実装。91件PDF処理（7件57字スキップ）、4,159 chunks生成。最大900字・平均660字・最小80字と適切サイズ。structured_dataから生成したchunk_context（大学名・入試方式・定員・出願期間）を各chunkに付与。pdf_chunksテーブルに保存完了。 |
| 2026-07 | **Phase 4C 実験完了（10大学）**：Cohere embed-multilingual-v3（Vortex外部ゲートウェイ経由）でEmbedding実施。API形式はinput+input_typeのOpenAI互換形式が正解（textsのみや内部K8sアドレスは不可）。4,159件全件成功・0件失敗。Qdrant Cloud pdf_chunksコレクション（1024次元Cosine）に全points保存完了。Phase 4A〜4C実験完了、RAGパイプラインのデータ準備が整った。次：Phase 4D 検索・回答生成実装。 |
| 2026-07 | **Phase 4D 実験完了（10大学）**：RAG検索・回答生成パイプライン実装完了。①Query Understanding（LLMで大学名・年度・入試方式を自動抽出）②Qdrant ベクトル検索（Cohere同一モデルでクエリembedding、payloadフィルタ）③LLM回答生成（出典URL付き）の3ステップ構成。SSL証明書問題をrequestsベース直接呼び出しで解決。Qdrant payload chunk_text を500字→900字に拡大（全件再embed済み）。検索品質：score 0.86~0.88（北見工業大学推薦定員95名を正確に回答）。課題：大阪大学等の複数研究科混在PDFでは学部情報のヒット精度が低い。 |
| 2026-07 | **Phase 4D バッチテスト完了（50問）**：10大学×5問=50問のRAGバッチテスト実施。成功率100%・エラー0件。平均score 0.8158（高精度≥0.85:22%、中精度0.75~:68%、低精度:10%）。大学別トップ：旭川医科大学0.8718、北見工業大学0.8512。課題：大阪大学(0.7546)・横浜国立大学(0.7703)はPDF内容混在（大学院・法科大学院・学部が混在）により学部入試チャンクがヒットしにくい。CIDフォント文字化けchunkが一部回答品質に影響。結果CSV：results/phase4d_test_20260618_162152.csv。次：全国展開準備。 |
| 2026-07 | **第五阶段启动：旧帝大7校教员信息爬取**：新增 `scripts/crawl_professors.py`（Selenium + researchmap.jp）和 `scripts/crawl_lab_urls.py`（DuckDuckGo搜索研究室URL）。Supabase 新建 `professor` 表，含researchmap_id/姓名/所属/职位/研究分野/关键词/lab_url等字段。当前已爬取约190件（東京大学90件+京都大学100件），东京大学URL收集2250条、京都大学1107条，Step1详情爬取进行中。Step2（研究室URL补全）尚未启动。主要问题：访问集中提示/ChromeDriver崩溃/渲染超时需重启脚本继续。 |

> **维护提示**：每次完成一个子任务或发现重要问题，请在此表格追加一行记录，并同步更新第 8 节的进度列表。