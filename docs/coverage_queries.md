# 覆盖率查询 SQL 手册

> 所有 SQL 均可直接在 **Supabase 控制台 → SQL Editor** 中运行。  
> 内置视图 `v_university_coverage` 和 `v_uncovered_units` 已在 `src/db/schema.sql` 中定义，无需额外建表。

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
9. [匹配方法质量分布](#9-匹配方法质量分布)

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
| 10215 | 11 | 10204 | 0.1 |

---

## 2. 按大学汇总覆盖率

### 使用内置视图（推荐）

```sql
-- 按覆盖率从低到高排列（最需要补爬的在前）
SELECT *
FROM v_university_coverage
ORDER BY coverage_pct ASC, university_name;
```

### 只看已完全覆盖的大学

```sql
SELECT *
FROM v_university_coverage
WHERE coverage_pct = 100
ORDER BY university_name;
```

### 只看覆盖率 50% 以上的大学

```sql
SELECT *
FROM v_university_coverage
WHERE coverage_pct >= 50
ORDER BY coverage_pct DESC;
```

---

## 3. 某所大学的详细覆盖情况

```sql
-- 把 '室蘭工業大学' 换成任意大学名
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

| unit_type | unit_name | sub_unit_name | last_found_year | status |
|-----------|-----------|---------------|-----------------|--------|
| 学部 | 理工学部 | 創造工学科 | 令和7年度 | ✓ covered |
| 学部 | 理工学部 | システム理化学科 | 令和7年度 | ✓ covered |
| 学部 | 工学部 | 建築社会基盤系学科 | NULL | ✗ uncovered |
| 研究科 | 工学研究科 | 工学専攻 | 令和7年度 | ✓ covered |

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

### 统计各大学爬取 PDF 数量

```sql
SELECT
    university_name,
    COUNT(*)                                            AS pdf_count,
    COUNT(*) FILTER (WHERE pdf_scope = 'undergraduate') AS undergrad_count,
    COUNT(*) FILTER (WHERE pdf_scope = 'graduate')      AS graduate_count,
    COUNT(*) FILTER (WHERE pdf_scope = 'combined')      AS combined_count,
    MAX(crawled_at)                                     AS last_crawled
FROM crawled_pdfs
GROUP BY university_name
ORDER BY pdf_count DESC;
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

### 查看某个 PDF 覆盖了哪些 unit

```sql
SELECT
    uu.university_name,
    uu.unit_type,
    uu.unit_name,
    uu.sub_unit_name,
    puc.match_method,
    puc.match_confidence
FROM pdf_unit_coverage  puc
JOIN university_units   uu  ON uu.id = puc.unit_id
JOIN crawled_pdfs       cp  ON cp.id = puc.pdf_id
WHERE cp.pdf_url = 'https://example.com/boshu.pdf'  -- 替换为实际 URL
ORDER BY uu.unit_type, uu.unit_name;
```

---

## 6. 未覆盖 unit 清单

### 全国未覆盖 unit（使用内置视图）

```sql
SELECT *
FROM v_uncovered_units
LIMIT 50;
```

### 指定大学的未覆盖 unit

```sql
SELECT *
FROM v_uncovered_units
WHERE university_name = '北見工業大学';
```

### 统计各大学未覆盖 unit 数量（优先补爬队列）

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

## 7. 按都道府县统计覆盖率

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

### 按日期统计每天新增覆盖的 unit 数

```sql
SELECT
    DATE(last_crawled_at)  AS crawl_date,
    COUNT(*)               AS newly_covered
FROM university_units
WHERE last_found_year = '令和7年度'
  AND last_crawled_at IS NOT NULL
GROUP BY DATE(last_crawled_at)
ORDER BY crawl_date DESC;
```

### 按日期统计每天新增的 PDF 数

```sql
SELECT
    DATE(crawled_at)  AS crawl_date,
    COUNT(*)          AS pdf_count,
    SUM(COUNT(*)) OVER (ORDER BY DATE(crawled_at))  AS cumulative_total
FROM crawled_pdfs
GROUP BY DATE(crawled_at)
ORDER BY crawl_date DESC;
```

---

## 9. 匹配方法质量分布

### 全局匹配方法统计

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

**返回示例：**

| match_method | match_confidence | count | pct |
|-------------|-----------------|-------|-----|
| exact | high | 45 | 78.0 |
| exact | medium | 8 | 13.8 |
| fuzzy | medium | 5 | 8.2 |

### 查找低置信度匹配（人工审核候选）

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
JOIN university_units   uu  ON uu.id = puc.unit_id
JOIN crawled_pdfs       cp  ON cp.id = puc.pdf_id
WHERE puc.match_confidence = 'low'
   OR puc.match_method = 'llm'
ORDER BY uu.university_name, puc.matched_at DESC;
```

---

## 快速参考

| 目的 | SQL / 视图 |
|------|------------|
| 整体覆盖率 | 查询 #1 或 `SELECT * FROM v_university_coverage LIMIT 1` |
| 各大学覆盖率排名 | `SELECT * FROM v_university_coverage ORDER BY coverage_pct ASC` |
| 未覆盖 unit 列表 | `SELECT * FROM v_uncovered_units` |
| 指定大学覆盖详情 | 查询 #3（替换大学名） |
| 低质量匹配审核 | 查询 #9 最后一条 |
