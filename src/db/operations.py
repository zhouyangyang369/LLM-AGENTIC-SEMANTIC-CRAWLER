"""
数据库操作层
封装所有 Supabase 表操作，业务逻辑与 DB 解耦
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from .supabase_client import get_supabase

logger = logging.getLogger(__name__)


DEFAULT_PAGE_SIZE = 1000


def _fetch_all_rows(table_name: str, columns: str = "*", page_size: int = DEFAULT_PAGE_SIZE) -> list[dict]:
    """
    分页读取 Supabase 表数据，避免默认最多返回 1000 行导致覆盖率统计不完整。
    仅用于简单全表读取场景；带复杂条件的查询由调用方自行处理。
    """
    sb = get_supabase()
    rows: list[dict] = []
    offset = 0

    while True:
        result = (
            sb.table(table_name)
            .select(columns)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    return rows


# ─────────────────────────────────────────────
# university_units 相关操作
# ─────────────────────────────────────────────

def upsert_university_unit(
    university_name: str,
    unit_type: str,
    unit_name: str,
    sub_unit_name: Optional[str] = None,
    prefecture: Optional[str] = None,
) -> dict:
    """
    插入或更新 university_units 记录。

    说明：PostgreSQL 的普通 UNIQUE 约束会把 NULL 当作不同值处理，
    因此 schema 使用 COALESCE(sub_unit_name, '') 的唯一表达式索引。
    Supabase/PostgREST 不能直接用表达式索引做 on_conflict，所以这里仍采用
    先查再写的方式；schema 中的唯一索引用于防止重复数据长期积累。
    """
    sb = get_supabase()

    query = (
        sb.table("university_units")
        .select("id")
        .eq("university_name", university_name)
        .eq("unit_type", unit_type)
        .eq("unit_name", unit_name)
    )
    if sub_unit_name:
        query = query.eq("sub_unit_name", sub_unit_name)
    else:
        query = query.is_("sub_unit_name", "null")

    existing = query.execute()

    payload = {
        "university_name": university_name,
        "unit_type": unit_type,
        "unit_name": unit_name,
        "sub_unit_name": sub_unit_name,
        "prefecture": prefecture,
    }

    if existing.data:
        record_id = existing.data[0]["id"]
        sb.table("university_units").update(payload).eq("id", record_id).execute()
        logger.debug("更新 unit: %s / %s / %s", university_name, unit_name, sub_unit_name)
        return {"id": record_id, "action": "updated"}

    result = sb.table("university_units").insert(payload).execute()
    record_id = result.data[0]["id"]
    logger.debug("插入 unit: %s / %s / %s", university_name, unit_name, sub_unit_name)
    return {"id": record_id, "action": "inserted"}


def get_uncovered_universities(target_year: str = "令和7年度") -> list[dict]:
    """
    返回本年度尚未覆盖的大学列表（去重，每所大学只出现一次）
    用于驱动爬取任务队列。

    注意：这里使用分页全量读取后在 Python 侧过滤，避免 PostgREST 默认分页
    以及日文年度字符串在 .or_ 过滤表达式中的转义问题。
    """
    rows = _fetch_all_rows(
        "university_units",
        "university_name, prefecture, last_found_year",
    )

    # 去重（同一大学多个 unit）
    seen = set()
    universities = []
    for row in rows:
        if row.get("last_found_year") == target_year:
            continue
        name = row["university_name"]
        if name not in seen:
            seen.add(name)
            universities.append({
                "university_name": name,
                "prefecture": row.get("prefecture"),
            })
    return universities


def get_units_for_university(university_name: str) -> list[dict]:
    """返回某所大学的所有 unit（用于后续 PDF 内容匹配）"""
    sb = get_supabase()
    result = (
        sb.table("university_units")
        .select("*")
        .eq("university_name", university_name)
        .execute()
    )
    return result.data


def mark_unit_found(unit_id: str, year: str = "令和7年度") -> None:
    """更新 unit 的 last_found_year 和 last_crawled_at"""
    sb = get_supabase()
    sb.table("university_units").update({
        "last_found_year": year,
        "last_crawled_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", unit_id).execute()


# ─────────────────────────────────────────────
# crawled_pdfs 相关操作
# ─────────────────────────────────────────────

def compute_sha256(raw_bytes: bytes) -> str:
    """计算字节流的 SHA-256 哈希"""
    return hashlib.sha256(raw_bytes).hexdigest()


def upsert_crawled_pdf(
    university_name: str,
    pdf_url: str,
    raw_bytes: bytes,
    pdf_scope: Optional[str] = None,
    academic_year: Optional[str] = None,
    extracted_units: Optional[dict] = None,
) -> dict:
    """
    插入爬取的 PDF 记录，或在内容未变化时只更新 crawled_at。
    
    返回：
        {"id": <uuid>, "action": "inserted" | "updated", "content_hash": <hash>}
    """
    sb = get_supabase()
    content_hash = compute_sha256(raw_bytes)

    # 检查是否存在相同 URL + 相同内容
    existing = (
        sb.table("crawled_pdfs")
        .select("id")
        .eq("pdf_url", pdf_url)
        .eq("content_hash", content_hash)
        .execute()
    )

    if existing.data:
        # 内容没变，只刷新 crawled_at
        record_id = existing.data[0]["id"]
        sb.table("crawled_pdfs").update({
            "crawled_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", record_id).execute()
        logger.info("PDF 内容未变化，更新 crawled_at: %s", pdf_url)
        return {"id": record_id, "action": "updated", "content_hash": content_hash}
    else:
        # 新内容（URL 变了 or 内容变了），插新行
        payload = {
            "university_name": university_name,
            "pdf_url": pdf_url,
            "content_hash": content_hash,
            "pdf_scope": pdf_scope,
            "academic_year": academic_year,
            "extracted_units": extracted_units,
        }
        result = sb.table("crawled_pdfs").insert(payload).execute()
        record_id = result.data[0]["id"]
        logger.info("插入新 PDF: %s (scope=%s)", pdf_url, pdf_scope)
        return {"id": record_id, "action": "inserted", "content_hash": content_hash}


# ─────────────────────────────────────────────
# pdf_unit_coverage 相关操作
# ─────────────────────────────────────────────

def upsert_coverage(
    pdf_id: str,
    unit_id: str,
    match_confidence: str = "high",
    match_method: str = "exact",
    target_year: str = "令和7年度",
) -> dict:
    """
    插入或更新 PDF-unit 覆盖关系，同时更新 university_units.last_found_year。
    """
    sb = get_supabase()

    # 检查是否已存在
    existing = (
        sb.table("pdf_unit_coverage")
        .select("id")
        .eq("pdf_id", pdf_id)
        .eq("unit_id", unit_id)
        .execute()
    )

    if existing.data:
        record_id = existing.data[0]["id"]
        sb.table("pdf_unit_coverage").update({
            "match_confidence": match_confidence,
            "match_method": match_method,
            "matched_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", record_id).execute()
        action = "updated"
    else:
        result = sb.table("pdf_unit_coverage").insert({
            "pdf_id": pdf_id,
            "unit_id": unit_id,
            "match_confidence": match_confidence,
            "match_method": match_method,
        }).execute()
        record_id = result.data[0]["id"]
        action = "inserted"

    # 同步更新 university_units.last_found_year
    mark_unit_found(unit_id, target_year)
    return {"id": record_id, "action": action}


# ─────────────────────────────────────────────
# 覆盖率统计
# ─────────────────────────────────────────────

def get_coverage_stats(target_year: str = "令和7年度") -> dict:
    """返回整体覆盖率统计"""
    all_units = _fetch_all_rows("university_units", "last_found_year")
    total = len(all_units)
    covered = sum(1 for r in all_units if r.get("last_found_year") == target_year)
    return {
        "total": total,
        "covered": covered,
        "uncovered": total - covered,
        "coverage_pct": round(covered * 100 / total, 1) if total > 0 else 0.0,
        "target_year": target_year,
    }


def get_per_university_coverage(target_year: str = "令和7年度") -> list[dict]:
    """返回每所大学的覆盖率，按覆盖率升序排列（最差的在前，便于重试）"""
    all_units = _fetch_all_rows(
        "university_units",
        "university_name, prefecture, last_found_year",
    )

    from collections import defaultdict
    stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "covered": 0, "prefecture": ""})
    for row in all_units:
        name = row["university_name"]
        stats[name]["total"] += 1
        stats[name]["prefecture"] = row.get("prefecture", "")
        if row.get("last_found_year") == target_year:
            stats[name]["covered"] += 1

    result = []
    for name, s in stats.items():
        pct = round(s["covered"] * 100 / s["total"], 1) if s["total"] > 0 else 0.0
        result.append({
            "university_name": name,
            "prefecture": s["prefecture"],
            "total_units": s["total"],
            "covered_units": s["covered"],
            "coverage_pct": pct,
        })
    result.sort(key=lambda x: (x["coverage_pct"], x["university_name"]))
    return result