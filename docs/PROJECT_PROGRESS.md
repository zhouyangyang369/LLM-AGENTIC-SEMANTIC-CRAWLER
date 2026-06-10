# 日本高校入试募集要项爬取系统 —— 项目进度说明书

> **用途**：本文档面向 AI Agent（或新加入的开发者），用于在每次新 Session 开始时快速恢复项目上下文。  
> **维护原则**：每次有功能变更、调试结论、阶段推进时，请同步更新本文档对应章节。  
> **最后更新**：2025-07

---

## 目录

1. [项目目标](#1-项目目标)
2. [整体技术栈](#2-整体技术栈)
3. [项目目录结构](#3-项目目录结构)
4. [数据库设计（Supabase）](#4-数据库设计supabase)
5. [第一阶段：强规则爬虫（Baseline）](#5-第一阶段强规则爬虫baseline)
6. [第二阶段：LLM Agentic 语义爬虫（ReAct）](#6-第二阶段llm-agentic-语义爬虫react)
7. [第三阶段：Ground Truth 驱动爬取（当前主线）](#7-第三阶段ground-truth-驱动爬取当前主线)
8. [当前进度与待办事项](#8-当前进度与待办事项)
9. [已知问题与注意事项](#9-已知问题与注意事项)
10. [快速启动命令速查](#10-快速启动命令速查)
11. [更新日志](#11-更新日志)

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
| 向量/语义搜索 | Tavily Search API |
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
| `extracted_units` | JSONB | LLM 提取的结构化结果（存档） |

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

## 8. 当前进度与待办事项

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

### 🔄 进行中 / 待做
- [ ] **数据质量问题**：文科省 Excel 含历史旧学部名（如工学部/理工学部并存），需清理或在 unit_matcher 中加入别名映射
- [ ] **全量批量爬取**：稳定后对全国大学执行 `crawl --limit N` 批量爬取
- [ ] **扩大测试范围**：选取国立/公立/私立各类型大学各5所，验证不同网站结构下的稳定性

### 🚧 已知难点
- 日本高校网站高度异构，部分大学无规整 Sitemap
- 部分大学募集要项 PDF 存放于子域名或第三方平台
- LLM Token 消耗需控制（`pdf_extractor.py` 已限制 12,000 字符截断）
- 私立大学数量庞大（8 册 Excel），全量爬取耗时可能较长
- **文科省 Excel 数据质量**：部分大学存在新旧学部名并存问题（如室蘭工業大学：工学部→理工学部改制），导致 ground truth 不准确，覆盖率虚低
- **Tavily 搜索域名污染**：搜索结果会混入其他大学的 PDF，当前无域名过滤，需后续加入大学官网域名白名单

---

## 9. 已知问题与注意事项

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
| **PDF 内容截断** | `pdf_extractor.py` 限制最大 12,000 字符，超长 PDF 自动截断送 LLM |
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

## 10. 快速启动命令速查

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

## 11. 更新日志

| 日期 | 更新内容 |
|------|----------|
| 2025-07 | 创建本进度说明书；第三阶段 Ground Truth 入库（university_units）已完成；crawl_graph 等后续模块待调试 |
| 2025-07 | **crawl_graph 调试完成**：修复 LLM 客户端（改用 Portkey 统一封装）、修复 temperature 参数兼容性（Claude opus-4 不支持）、修复 bytes 存 State 内存问题、安装缺失依赖（pdfplumber/rapidfuzz/supabase）。端到端测试通过：室蘭工業大学 23 PDF 全部下载+提取+写库，exact 精确匹配，SHA-256 去重正常。遗留：文科省 Excel 含历史旧学部名（工学部/理工学部并存），属数据质量问题非代码 Bug。 |
| 2025-07 | **CLI 入口验证完成**：`run_phase3.py crawl` 和 `report` 子命令全部通过。室蘭工業大学(60%)、北見工業大学(71%)正常爬取。注意：全局参数（`--log-level`等）必须放在子命令名称之前。 |
| 2025-07 | **Phase3 重大改进**：①精准搜索——基于 Ground Truth 学部/研究科名生成精准查询，目标域名自动推断优先排序；②相关性过滤——新增 `node_filter_url` 节点，规则过滤 janu.jp/benesse/keinet/dnc.ac.jp 等无关域名，LLM 判断模糊URL；③LLM 后端切换至 JV Vortex（Claude Sonnet 4，OpenAI 兼容接口）并修复 llm_call 缩进 bug；验证结果：北見工業大学覆盖率 71%→100%，耗时节省 69%。 |

> **维护提示**：每次完成一个子任务或发现重要问题，请在此表格追加一行记录，并同步更新第 8 节的进度列表。