-- ============================================================
-- 第三阶段数据库 Schema
-- Ground Truth 驱动爬取，支持覆盖率追踪
-- ============================================================

-- gen_random_uuid() 需要 pgcrypto；Supabase 通常默认可用，这里显式声明便于迁移。
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 表1: Ground Truth（文部科学省 Excel 解析结果，静态基准）
-- 只存学校结构，不混入爬取状态
CREATE TABLE IF NOT EXISTS university_units (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  university_name   TEXT        NOT NULL,
  unit_type         TEXT        NOT NULL CHECK (unit_type IN ('学部', '研究科')),
  unit_name         TEXT        NOT NULL,
  sub_unit_name     TEXT,                    -- 学科名（学部用）or 専攻名（研究科用）
  prefecture        TEXT,                    -- 都道府県
  last_found_year   TEXT,                    -- '令和7年度'，NULL = 从未找到
  last_crawled_at   TIMESTAMPTZ,
  created_at        TIMESTAMPTZ DEFAULT now()
);

-- 表2: 爬取到的 PDF
-- 每次内容变化才插新行，URL 不变内容不变只更新 crawled_at
CREATE TABLE IF NOT EXISTS crawled_pdfs (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  university_name  TEXT        NOT NULL,
  pdf_url          TEXT        NOT NULL,
  content_hash     TEXT        NOT NULL,     -- SHA-256，检测内容是否变化
  pdf_scope        TEXT        CHECK (pdf_scope IN ('undergraduate', 'graduate', 'combined')),
  academic_year    TEXT,                     -- '令和7年度'
  extracted_units  JSONB,                    -- LLM 提取的原始结构化结果，存档用
  crawled_at       TIMESTAMPTZ DEFAULT now(),
  UNIQUE (pdf_url, content_hash)
);

-- 表3: 中间表（PDF 覆盖了哪些 unit）
-- 解决 PDF 与 unit 的多对多关系
CREATE TABLE IF NOT EXISTS pdf_unit_coverage (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  pdf_id           UUID        NOT NULL REFERENCES crawled_pdfs(id) ON DELETE CASCADE,
  unit_id          UUID        NOT NULL REFERENCES university_units(id) ON DELETE CASCADE,
  match_confidence TEXT        CHECK (match_confidence IN ('high', 'medium', 'low')),
  match_method     TEXT        CHECK (match_method IN ('exact', 'fuzzy', 'llm')),
  matched_at       TIMESTAMPTZ DEFAULT now(),
  UNIQUE (pdf_id, unit_id)
);

-- ============================================================
-- 索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_university_units_name   ON university_units (university_name);
CREATE INDEX IF NOT EXISTS idx_university_units_pref   ON university_units (prefecture);
CREATE INDEX IF NOT EXISTS idx_university_units_year   ON university_units (last_found_year);
CREATE UNIQUE INDEX IF NOT EXISTS uq_university_units_identity
  ON university_units (university_name, unit_type, unit_name, COALESCE(sub_unit_name, ''));
CREATE INDEX IF NOT EXISTS idx_crawled_pdfs_university ON crawled_pdfs (university_name);
CREATE INDEX IF NOT EXISTS idx_crawled_pdfs_year       ON crawled_pdfs (academic_year);
CREATE INDEX IF NOT EXISTS idx_coverage_unit           ON pdf_unit_coverage (unit_id);
CREATE INDEX IF NOT EXISTS idx_coverage_pdf            ON pdf_unit_coverage (pdf_id);

-- ============================================================
-- 常用视图
-- ============================================================

-- 视图: 各大学覆盖率汇总
CREATE OR REPLACE VIEW v_university_coverage AS
SELECT
  university_name,
  prefecture,
  COUNT(*)                                              AS total_units,
  COUNT(*) FILTER (WHERE last_found_year = '令和7年度') AS covered_units,
  ROUND(
    COUNT(*) FILTER (WHERE last_found_year = '令和7年度') * 100.0 / COUNT(*),
    1
  )                                                     AS coverage_pct
FROM university_units
GROUP BY university_name, prefecture
ORDER BY coverage_pct DESC, university_name;

-- 视图: 未覆盖的 unit（需要重试）
CREATE OR REPLACE VIEW v_uncovered_units AS
SELECT
  university_name,
  prefecture,
  unit_type,
  unit_name,
  sub_unit_name,
  last_found_year,
  last_crawled_at
FROM university_units
WHERE last_found_year IS NULL
   OR last_found_year != '令和7年度'
ORDER BY university_name, unit_type, unit_name;