"""
Pydantic データモデル — Agent 全体で共有する型定義。
"""

from __future__ import annotations

from typing import Annotated, Any
from pydantic import BaseModel, Field


# ── PDF エントリ ─────────────────────────────────────────────────────
class PDFEntry(BaseModel):
    url: str
    text: str = ""
    category: str = "不明"          # 学部 / 修士 / 博士 / 専門職 / 不明
    year: str = ""
    department: str = ""
    admission_type: str = "不明"    # 一般 / 推薦 / 外国人 / 社会人 / 不明
    source_page: str = ""


# ── サブサイト情報 ───────────────────────────────────────────────────
class SubsiteInfo(BaseModel):
    name: str
    url: str
    is_different_domain: bool = False
    category: str = "unknown"       # graduate / undergraduate / both / unknown


# ── LLM フィルタ結果 ─────────────────────────────────────────────────
class FilterResult(BaseModel):
    relevant_urls: list[str] = Field(default_factory=list)
    reason: str = ""


class NavigationResult(BaseModel):
    navigation_pages: list[dict] = Field(default_factory=list)


class SubsiteResult(BaseModel):
    subsites: list[SubsiteInfo] = Field(default_factory=list)


class PageExtractionResult(BaseModel):
    pdfs: list[PDFEntry] = Field(default_factory=list)
    follow_links: list[dict] = Field(default_factory=list)
    has_more: bool = False


class AuditResult(BaseModel):
    known_departments: list[str] = Field(default_factory=list)
    missing_departments: list[str] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)
    is_complete: bool = False
    completeness_note: str = ""


# ── Agent 状態 ───────────────────────────────────────────────────────
class AgentState(BaseModel):
    """LangGraph ノード間で受け渡す状態オブジェクト"""

    # 基本情報
    school_name: str
    official_url: str = ""
    sitemap_url: str = ""
    domain: str = ""

    # サイトマップから取得した全 URL
    all_sitemap_urls: list[str] = Field(default_factory=list)

    # LLM が選別した候補 URL
    candidate_pages: list[str] = Field(default_factory=list)

    # ナビゲーションページ → サブサイト
    navigation_pages: list[dict] = Field(default_factory=list)
    discovered_subsites: list[SubsiteInfo] = Field(default_factory=list)

    # クロール済みページ（ループ防止）
    visited_pages: set[str] = Field(default_factory=set)

    # 収集済み PDF
    pdfs: list[PDFEntry] = Field(default_factory=list)
    seen_pdf_urls: set[str] = Field(default_factory=set)

    # フォローすべきリンクキュー
    follow_queue: list[str] = Field(default_factory=list)

    # 完備性審査
    audit_rounds: int = 0
    found_departments: list[str] = Field(default_factory=list)
    missing_departments: list[str] = Field(default_factory=list)
    is_complete: bool = False

    # エラー・ログ
    errors: list[str] = Field(default_factory=list)
    decision_trace: list[dict] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True


# ── 最終出力 ─────────────────────────────────────────────────────────
class SchoolResult(BaseModel):
    school: str
    official_url: str = ""
    domain: str = ""
    sitemap_url: str = ""
    candidate_pages: list[str] = Field(default_factory=list)
    discovered_subsites: list[SubsiteInfo] = Field(default_factory=list)
    pdfs: list[PDFEntry] = Field(default_factory=list)
    found_departments: list[str] = Field(default_factory=list)
    missing_departments: list[str] = Field(default_factory=list)
    is_complete: bool = False
    errors: list[str] = Field(default_factory=list)
    decision_trace: list[dict] = Field(default_factory=list)
