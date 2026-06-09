# 第三阶段：Ground Truth 驱动爬取架构

## 概述

以文部科学省公开数据作为 **ground truth**，实现目标驱动爬取。  
爬取前就知道每所大学有哪些学部/研究科/専攻，爬完后可以精确计算覆盖率，知道哪些没爬到、需要重试。

## 文件结构

```
src/
├── db/
│   ├── supabase_client.py    # Supabase 单例客户端
│   ├── operations.py         # 所有 DB 操作（upsert / query）
│   └── schema.sql            # 完整建表 SQL（含索引和视图）
│
├── pipeline/
│   └── phase3/
│       ├── mext_downloader.py   # 从文科省网站下载 Excel
│       ├── mext_excel_parser.py # 解析 Excel → 结构化记录
│       ├── mext_importer.py     # 批量导入 university_units 表
│       ├── pdf_downloader.py    # 带重试的 PDF 下载器
│       ├── pdf_extractor.py     # pdfplumber + LLM 提取
│       ├── unit_matcher.py      # exact/fuzzy/llm 三级匹配
│       ├── crawl_graph.py       # LangGraph 编排主图
│       ├── batch_crawler.py     # 批量任务调度
│       └── coverage_report.py  # 覆盖率报告输出
│
└── utils/
    └── logger.py             # 统一日志配置

run_phase3.py                 # 统一 CLI 入口
```

## 数据库设计

### 三张核心表

| 表名 | 作用 |
|------|------|
| `university_units` | Ground truth，文科省 Excel 解析结果（静态基准） |
| `crawled_pdfs` | 已爬取的 PDF，用 SHA-256 去重，内容变化才插新行 |
| `pdf_unit_coverage` | PDF 与 unit 的多对多关系，记录匹配方式和置信度 |

### 关键设计决策

- **`last_found_year`** 代替模糊的 `coverage_status`，语义清晰，支持定期更新
- **`content_hash`**（SHA-256）检测同 URL 内容是否变化，防止重复插入
- `pdf_unit_coverage.match_method` 区分 `exact` / `fuzzy` / `llm`，便于后续数据质量审查

## LangGraph 爬取流程

```
load_units
    │
    ▼
search_pdfs  ← Tavily 搜索候选 PDF URL
    │
    ▼
pick_url  ←──────────────────────┐
    │                             │
    ▼                             │ should_continue=True
download  ────(失败)──→ check_coverage
    │ (成功)                       ▲
    ▼                             │
extract   ← pdfplumber + LLM      │
    │                             │
    ▼                             │
match_save ─────────────────────→ check_coverage
```

### 覆盖率驱动的停止条件

- 该大学所有 unit 均已匹配 → 停止
- 没有更多候选 URL → 停止
- 否则继续处理下一个 URL

## Unit 匹配策略

| 方法 | 条件 | confidence |
|------|------|------------|
| `exact` | 字符串完全一致（标准化后） | high |
| `fuzzy` | rapidfuzz ≥ 90分 | high/medium |
| `llm` | rapidfuzz 75~90分，不确定 | medium/low |
| 跳过 | < 75分 | — |

## 快速开始

### 1. 环境准备

```bash
pip install -r requirements_phase3.txt
# 手动创建 .env，并填写下列变量：
# SUPABASE_URL=https://your-project.supabase.co
# SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
# TAVILY_API_KEY=your-tavily-api-key
# OPENAI_API_KEY=your-openai-api-key
# LLM_MODEL=gpt-4o-mini
```

### 2. 数据库初始化

在 Supabase 控制台执行 `src/db/schema.sql`。

### 3. 导入 Ground Truth

```bash
# 下载文科省 Excel
python run_phase3.py download-excel

# 试运行（只解析，不写库）
python run_phase3.py import-excel --excel data/R06_daigaku.xlsx --dry-run

# 正式导入
python run_phase3.py import-excel --excel data/R06_daigaku.xlsx
```

### 4. 爬取 PDF

```bash
# 爬取指定大学
python run_phase3.py crawl --universities 北海道大学 東北大学 東京大学

# 批量爬取未覆盖大学（先从10所开始测试）
python run_phase3.py crawl --limit 10

# 全量爬取（并发2线程）
python run_phase3.py crawl --max-workers 2
```

### 5. 查看覆盖率

```bash
# 终端输出
python run_phase3.py report

# 只看未完全覆盖的大学 + 导出 CSV
python run_phase3.py report --uncovered-only --output report.csv
```

## 覆盖率查询 SQL

```sql
-- 整体覆盖率
SELECT covered, total, coverage_pct FROM v_university_coverage LIMIT 1;

-- 哪些 unit 今年还没爬到
SELECT * FROM v_uncovered_units LIMIT 20;

-- 按大学汇总覆盖率
SELECT * FROM v_university_coverage ORDER BY coverage_pct ASC;
```

## 注意事项

1. **不影响原有 baseline**：`run_single.py` / `run_batch.py` / `run_react.py` 相关代码保持不变。
2. **Excel 结构变化**：文科省每年度 Excel 列结构可能微调，`mext_excel_parser.py` 提供了自适应表头识别 + fallback 解析。
3. **反爬保护**：`pdf_downloader.py` 内置 User-Agent 轮换和指数退避重试；批量爬取建议 `--max-workers 1~2`。
4. **LLM Token 控制**：`pdf_extractor.py` 限制最大 12,000 字符，超长 PDF 自动截断。