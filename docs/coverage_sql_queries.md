# 覆盖率查询 SQL 速查手册

> **用途**：在 Supabase 控制台 → SQL Editor 中直接粘贴运行，查看爬取进度和覆盖率。  
> **前提**：已执行 `src/db/schema.sql`，三张核心表和两个视图均已创建。

---

## 目录

1. [整体覆盖率（一行总览）](#1-整体覆盖率一行总览)
2. [按大学汇总覆盖率](#2-按大学汇总覆盖率)
3. [某所大学的详细覆盖情况](#3-某所大学的详细覆盖情况)
4. [某大学爬取了哪些 PDF](#4-某大学爬取了哪些-pdf)
5. [PDF 具体覆盖了哪些 unit（三表联查）](#5-pdf-具体覆盖了哪些-unit三表联查)
6. [未覆盖 unit 清单](#6-未覆盖-unit-清单)
7. [按都道府县统计覆盖率](#7-按都道府县统计覆盖率)
8. [爬取进度时间线](#8-爬取进度时间线)
9. [数据质量检查](#9-数据质量检查)

---

## 1. 整体覆盖率（一行总览）

```sql
SELECT
    COUNT(*)                                                      AS total_units,
    COUNT(*) FILTER (WHERE last_found_year = '令和7年度')         AS covered_units,
    COUNT(*) FILTER (WHERE last_found_year IS NULL
                        OR last_found_year != '令和7年度')        AS uncovered_units,
    ROUND(
        COUNT(*) FILTER (WHERE last_found_year = '令和7年度')
        * 100.0 / COUNT(*), 1
    )                                                             AS coverage_pct
FROM university_units;
```

**返回示例：**

| total_units | covered_units | uncovered_units | coverage_pct |
|-------------|---------------|-----------------|--------------|
| 10215       | 11            | 10204           | 0.1          |

---

## 2. 按大学汇总覆盖率

### 2-1. 使用内置视图（推荐）

```sql
-- 按覆盖率从低到高排列（最需要补爬的在前）
SELECT
    university_name,
    prefecture,
    total_units,
    covered_units,
    coverage_pct
FROM v_university_coverage
ORDER BY coverage_pct ASC, university_name;
```

### 2-2. 只看已有进展的大学（覆盖率 > 0）

```sql
SELECT
    university_name,
    prefecture,
    total_units,
    covered_units,
    coverage_pct
FROM v_university_coverage
WHERE coverage_pct > 0
ORDER BY coverage_pct DESC;
```

### 2-3. 只看完全覆盖的大学（100%）

```sql
SELECT
    university_name,
    prefecture,
    total_units,
    covered_units
FROM v_university_coverage
WHERE coverage_pct = 100.0
ORDER BY university_name;
```

### 2-4. 未完全覆盖的大学数量统计

```sql
SELECT
    COUNT(*) FILTER (WHERE coverage_pct = 100.0)  AS fully_covered,
    COUNT(*) FILTER (WHERE coverage_pct > 0
                      AND coverage_pct < 100.0)   AS partially_covered,
    COUNT(*) FILTER (WHERE coverage_pct = 0.0)    AS not_started,
    COUNT(*)                                       AS total_universities
FROM v_university_coverage;
```

---

## 3. 某所大学的详细覆盖情况

> 将 `'室蘭工業大学'` 替换为任意大学名。

```sql
SELECT
    unit_type,
    unit_name,
    sub_unit_name,
    last_found_year,
    last_crawled_at,
    CASE
        WHEN last_found_year = '令和7年度' THEN '✓ covered'
        ELSE '✗ uncovered'
    END AS status
FROM university_units
WHERE university_name = '室蘭工業大学'
ORDER BY unit_type, unit_name, sub_unit_name;
```

**返回示例：**

| unit_type | unit_name  | sub_unit_name      | last_found_year | status     |
|-----------|------------|--------------------|-----------------|------------|
| 学部      | 工学部     | 応用理化学系学科   | NULL            | ✗ uncovered|
| 学部      | 理工学部   | システム理化学科   | 令和7年度       | ✓ covered  |
| 研究科    | 工学研究科 | 工学専攻           | 令和7年度       | ✓ covered  |

---

## 4. 某大学爬取了哪些 PDF

```sql
SELECT
    pdf_url,
    pdf_scope,
    academic_year,
    crawled_at
FROM crawled_pdfs
WHERE university_name = '室蘭工業大学'
ORDER BY crawled_at DESC;
```

### 4-1. 按 scope 统计 PDF 数量

```sql
SELECT
    university_name,
    pdf_scope,
    COUNT(*) AS pdf_count
FROM crawled_pdfs
WHERE university_name = '室蘭工業大学'
GROUP BY university_name, pdf_scope
ORDER BY pdf_scope;
```

---

## 5. PDF 具体覆盖了哪些 unit（三表联查）

```sql
SELECT
    cp.pdf_url,
    cp.pdf_scope,
    uu.unit_type,
    uu.unit_name,
    uu.sub_unit_name,
    puc.match_method,
    puc.match_confidence,
    puc.matched_at
FROM pdf_unit_coverage  puc
JOIN crawled_pdfs       cp  ON cp.id  = puc.pdf_id
JOIN university_units   uu  ON uu.id  = puc.unit_id
WHERE uu.university_name = '室蘭工業大学'
ORDER BY puc.matched_at DESC;
```

### 5-1. 只看 fuzzy 或 llm 匹配的记录（需人工复核）

```sql
SELECT
    uu.university_name,
    uu.unit_type,
    uu.unit_name,
    uu.sub_unit_name,
    puc.match_method,
    puc.match_confidence,
    cp.pdf_url
FROM pdf_unit_coverage  puc
JOIN crawled_pdfs       cp  ON cp.id  = puc.pdf_id
JOIN university_units   uu  ON uu.id  = puc.unit_id
WHERE puc.match_method IN ('fuzzy', 'llm')
ORDER BY puc.match_confidence ASC, uu.university_name;
```

---

## 6. 未覆盖 unit 清单

### 6-1. 使用内置视图（推荐）

```sql
-- 全国所有还没爬到的 unit
SELECT *
FROM v_uncovered_units
LIMIT 50;
```

### 6-2. 指定某所大学的未覆盖 unit

```sql
SELECT
    unit_type,
    unit_name,
    sub_unit_name,
    last_found_year,
    last_crawled_at
FROM v_uncovered_units
WHERE university_name = '北見工業大学';
```

### 6-3. 统计各大学未覆盖 unit 数量（用于制定重试优先级）

```sql
SELECT
    university_name,
    prefecture,
    COUNT(*) AS uncovered_count
FROM v_uncovered_units
GROUP BY university_name, prefecture
ORDER BY uncovered_count DESC
LIMIT 30;
```

---

## 7. 按都道府県统计覆盖率

```sql
SELECT
    prefecture,
    COUNT(DISTINCT university_name)                                    AS universities,
    COUNT(*)                                                           AS total_units,
    COUNT(*) FILTER (WHERE last_found_year = '令和7年度')              AS covered_units,
    ROUND(
        COUNT(*) FILTER (WHERE last_found_year = '令和7年度')
        * 100.0 / COUNT(*), 1
    )                                                                  AS coverage_pct
FROM university_units
GROUP BY prefecture
ORDER BY coverage_pct DESC, prefecture;
```

---

## 8. 爬取进度时间线

### 8-1. 每天新增 PDF 数量

```sql
SELECT
    DATE(crawled_at)  AS crawl_date,
    COUNT(*)          AS new_pdfs,
    COUNT(DISTINCT university_name) AS universities_processed
FROM crawled_pdfs
GROUP BY DATE(crawled_at)
ORDER BY crawl_date DESC;
```

### 8-2. 每天新增覆盖 unit 数量

```sql
SELECT
    DATE(last_crawled_at)  AS crawl_date,
    COUNT(*)               AS newly_covered_units
FROM university_units
WHERE last_found_year = '令和7年度'
  AND last_crawled_at IS NOT NULL
GROUP BY DATE(last_crawled_at)
ORDER BY crawl_date DESC;
```

### 8-3. 最近爬取的大学列表

```sql
SELECT
    university_name,
    MAX(crawled_at) AS last_crawled
FROM crawled_pdfs
GROUP BY university_name
ORDER BY last_crawled DESC
LIMIT 20;
```

---

## 9. 数据质量检查

### 9-1. 检查重复 unit（同一大学/学部/学科出现多次）

```sql
SELECT
    university_name,
    unit_type,
    unit_name,
    COALESCE(sub_unit_name, '') AS sub_unit_name,
    COUNT(*) AS duplicate_count
FROM university_units
GROUP BY university_name, unit_type, unit_name, COALESCE(sub_unit_name, '')
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC;
```

### 9-2. 检查 PDF 内容变化（同一 URL 多个 hash）

```sql
SELECT
    pdf_url,
    COUNT(DISTINCT content_hash) AS hash_versions,
    MIN(crawled_at)              AS first_crawled,
    MAX(crawled_at)              AS last_crawled
FROM crawled_pdfs
GROUP BY pdf_url
HAVING COUNT(DISTINCT content_hash) > 1
ORDER BY hash_versions DESC;
```

### 9-3. 检查 low confidence 的匹配记录（可能误匹配）

```sql
SELECT
    uu.university_name,
    uu.unit_type,
    uu.unit_name,
    uu.sub_unit_name,
    puc.match_method,
    puc.match_confidence,
    cp.pdf_url
FROM pdf_unit_coverage  puc
JOIN crawled_pdfs       cp  ON cp.id  = puc.pdf_id
JOIN university_units   uu  ON uu.id  = puc.unit_id
WHERE puc.match_confidence = 'low'
ORDER BY uu.university_name;
```

### 9-4. 统计各 match_method 的使用比例

```sql
SELECT
    match_method,
    match_confidence,
    COUNT(*) AS count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct
FROM pdf_unit_coverage
GROUP BY match_method, match_confidence
ORDER BY match_method, match_confidence;
```

---

## 快速参考：内置视图说明

| 视图名 | 用途 |
|--------|------|
| `v_university_coverage` | 各大学覆盖率汇总（university_name / total_units / covered_units / coverage_pct） |
| `v_uncovered_units` | 本年度尚未覆盖的所有 unit 清单，可直接用于制定重试队列 |

> **提示**：以上所有 SQL 中的年度字符串 `'令和7年度'` 可按需替换为其他年度（如 `'令和8年度'`）。