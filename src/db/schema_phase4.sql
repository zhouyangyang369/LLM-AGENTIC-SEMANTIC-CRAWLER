-- ============================================================
-- Phase 4 データベース変更 SQL
-- Supabase SQL Editor で実行してください
-- ============================================================

-- ============================================================
-- 1. crawled_pdfs テーブルへのフィールド追加
-- ============================================================

ALTER TABLE crawled_pdfs
  ADD COLUMN IF NOT EXISTS doc_type          TEXT,
  ADD COLUMN IF NOT EXISTS actual_year       TEXT,
  ADD COLUMN IF NOT EXISTS is_scan_pdf       BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_cleaned        BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_excluded       BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS exclusion_reason  TEXT,
  -- Phase 4A: PDF 全文構造化結果
  ADD COLUMN IF NOT EXISTS full_text         TEXT,      -- pdfplumber + 表格Markdown 全文
  ADD COLUMN IF NOT EXISTS page_count        INTEGER,   -- PDFページ数
  ADD COLUMN IF NOT EXISTS char_count        INTEGER;   -- 抽出文字数

COMMENT ON COLUMN crawled_pdfs.doc_type         IS '文書種別: 募集要項/選抜要項/出願要領/合格発表/便覧/その他';
COMMENT ON COLUMN crawled_pdfs.actual_year      IS 'extracted_units から読み取った実際の年度（academic_yearの修正値）';
COMMENT ON COLUMN crawled_pdfs.is_scan_pdf      IS 'スキャン版PDF（pdfplumber文字数<500）フラグ';
COMMENT ON COLUMN crawled_pdfs.is_cleaned       IS '前置清洗処理済みフラグ';
COMMENT ON COLUMN crawled_pdfs.is_excluded      IS '除外フラグ（非募集要項・無関係ドメイン等）';
COMMENT ON COLUMN crawled_pdfs.exclusion_reason IS '除外理由メモ';

-- インデックス
CREATE INDEX IF NOT EXISTS idx_crawled_pdfs_doc_type    ON crawled_pdfs(doc_type);
CREATE INDEX IF NOT EXISTS idx_crawled_pdfs_actual_year ON crawled_pdfs(actual_year);
CREATE INDEX IF NOT EXISTS idx_crawled_pdfs_is_excluded ON crawled_pdfs(is_excluded);
CREATE INDEX IF NOT EXISTS idx_crawled_pdfs_is_scan     ON crawled_pdfs(is_scan_pdf);


-- ============================================================
-- 2. pdf_chunks テーブル（新規作成）
-- ============================================================

-- ※ pgvector は使用しない（ベクトルは Qdrant Cloud に保存）
CREATE TABLE IF NOT EXISTS pdf_chunks (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- 親PDF参照
  pdf_id           UUID NOT NULL REFERENCES crawled_pdfs(id) ON DELETE CASCADE,
  pdf_url          TEXT,

  -- 検索フィルタ用メタデータ（Phase 3 資産）
  university_name  TEXT NOT NULL,
  unit_name        TEXT,                    -- 学部/研究科名（NULLの場合は全学共通）
  unit_type        TEXT,                    -- 学部 / 研究科
  academic_year    TEXT,                    -- 令和7年度 等
  pdf_scope        TEXT,                    -- undergraduate / graduate / combined

  -- Chunk 本体
  chunk_index      INTEGER NOT NULL,        -- PDF内の連番
  chunk_text       TEXT NOT NULL,           -- chunk 本文（500~800字）
  chunk_context    TEXT,                    -- LLMが付与したcontext説明（1~2文）
  chunk_text_with_context TEXT,             -- context + chunk_text 結合（embedding用）

  -- 構造情報
  section_path     TEXT,                    -- 章節パス: 第3章出願手続>3.1一般選抜
  page_number      INTEGER,                 -- ページ番号
  page_range       TEXT,                    -- ページ範囲: 12-13

  -- 入試方式タグ（LLM付与）
  exam_types       TEXT[],                  -- {一般選抜, 前期日程} 等

  -- ※ ベクトルは Qdrant Cloud に保存（chunk_id で紐付け）
  -- embedding はこのテーブルには持たない

  -- 管理
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- コメント
COMMENT ON TABLE pdf_chunks IS 'Phase 4B: PDF を Contextual Chunking した結果。RAG 検索の基本単位。';
COMMENT ON COLUMN pdf_chunks.chunk_context IS 'Anthropic Contextual Retrieval 手法による文書全体コンテキスト（1~2文）';
COMMENT ON COLUMN pdf_chunks.chunk_text_with_context IS 'embedding 生成用: chunk_context + chunk_text の結合テキスト';
COMMENT ON COLUMN pdf_chunks.exam_types IS '入試方式タグ配列: 一般選抜/学校推薦型/総合型/社会人/外国人等';

-- インデックス
CREATE INDEX IF NOT EXISTS idx_pdf_chunks_pdf_id          ON pdf_chunks(pdf_id);
CREATE INDEX IF NOT EXISTS idx_pdf_chunks_university       ON pdf_chunks(university_name);
CREATE INDEX IF NOT EXISTS idx_pdf_chunks_unit             ON pdf_chunks(unit_name);
CREATE INDEX IF NOT EXISTS idx_pdf_chunks_year             ON pdf_chunks(academic_year);
CREATE INDEX IF NOT EXISTS idx_pdf_chunks_scope            ON pdf_chunks(pdf_scope);
CREATE INDEX IF NOT EXISTS idx_pdf_chunks_exam_types       ON pdf_chunks USING GIN(exam_types);

-- ※ ベクトルインデックスは Qdrant Cloud 側で管理
-- Qdrant の payload フィルタ用に以下フィールドを Qdrant にも複製して保存:
--   chunk_id, university_name, unit_name, academic_year, pdf_scope, exam_types


-- ============================================================
-- 3. 便利ビュー
-- ============================================================

-- Phase 4 前置清洗の進捗確認ビュー
CREATE OR REPLACE VIEW v_phase4_clean_progress AS
SELECT
  university_name,
  COUNT(*)                                          AS total_pdfs,
  COUNT(*) FILTER (WHERE is_excluded = TRUE)        AS excluded,
  COUNT(*) FILTER (WHERE is_scan_pdf = TRUE)        AS scan_pdfs,
  COUNT(*) FILTER (WHERE is_cleaned = TRUE)         AS cleaned,
  COUNT(*) FILTER (WHERE doc_type IS NOT NULL)      AS classified,
  COUNT(*) FILTER (WHERE actual_year IS NOT NULL)   AS year_fixed,
  ROUND(COUNT(*) FILTER (WHERE is_cleaned = TRUE)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1)             AS clean_pct
FROM crawled_pdfs
GROUP BY university_name
ORDER BY university_name;

-- Phase 4B chunking 進捗確認ビュー
CREATE OR REPLACE VIEW v_phase4_chunk_progress AS
SELECT
  c.university_name,
  COUNT(DISTINCT c.id)                             AS total_pdfs,
  COUNT(DISTINCT ch.pdf_id)                        AS chunked_pdfs,
  COUNT(ch.id)                                     AS total_chunks
FROM crawled_pdfs c
LEFT JOIN pdf_chunks ch ON ch.pdf_id = c.id
WHERE c.is_excluded IS NOT TRUE
GROUP BY c.university_name
ORDER BY c.university_name;