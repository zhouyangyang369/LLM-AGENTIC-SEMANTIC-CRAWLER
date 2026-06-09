"""
Unit 匹配器
将 LLM 提取的学部/研究科名与 ground truth (university_units) 对齐。

匹配策略（按优先级）：
  1. exact  : 字符串完全一致
  2. fuzzy  : rapidfuzz 模糊匹配（相似度 ≥ FUZZY_THRESHOLD）
  3. llm    : 前两种没把握时（阈值 FUZZY_MEDIUM 到 FUZZY_THRESHOLD 之间），
              标记为 llm 待后续人工/LLM 二次确认
  4. 低于 FUZZY_MEDIUM : 跳过（不匹配）
"""
from __future__ import annotations

import logging
import unicodedata
from typing import Optional

try:
    from rapidfuzz import fuzz, process as rfprocess
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False
    logging.getLogger(__name__).warning(
        "rapidfuzz 未安装，退化为纯精确匹配。建议: pip install rapidfuzz"
    )

logger = logging.getLogger(__name__)

# 模糊匹配阈值
FUZZY_THRESHOLD = 90   # ≥ 90 → fuzzy (high/medium)
FUZZY_MEDIUM    = 75   # 75~90 → llm
# 75 以下不匹配


def _normalize(text: str) -> str:
    """标准化文字：NFKC 规范化，去除空格和常见分隔符。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    # 去除空白和常见中点/分隔符，降低「情報・工学」等写法差异的影响
    for token in [" ", "\u3000", "・", "･", "／"]:
        text = text.replace(token, "")
    return text.strip()


def _build_unit_key(unit: dict) -> str:
    """组合 unit_name + sub_unit_name 作为匹配键"""
    name = _normalize(unit.get("unit_name", ""))
    sub = _normalize(unit.get("sub_unit_name") or "")
    return f"{name}/{sub}" if sub else name


def match_units(
    extracted_units: list[dict],
    known_units: list[dict],
) -> list[dict]:
    """
    将 LLM 提取的 covered_units 与 ground truth known_units 对齐。

    Args:
        extracted_units: LLM 提取的 covered_units 列表
            格式: [{"unit_type": ..., "unit_name": ..., "sub_units": [...], "confidence": ...}]
        known_units: university_units 表中的记录列表
            格式: [{"id": ..., "unit_type": ..., "unit_name": ..., "sub_unit_name": ..., ...}]

    Returns:
        匹配结果列表:
            [{"unit_id": ..., "unit_name": ..., "confidence": ..., "method": ...}]
    """
    if not extracted_units or not known_units:
        return []

    # 构建 ground truth 查找表
    # key: normalized unit_name (或 unit_name/sub_unit_name)
    gt_index: dict[str, dict] = {}
    for ku in known_units:
        key = _build_unit_key(ku)
        gt_index[key] = ku
        # 也索引只有 unit_name 的 key（不含 sub）
        name_only = _normalize(ku.get("unit_name", ""))
        if name_only not in gt_index:
            gt_index[name_only] = ku

    results: list[dict] = []
    matched_unit_ids: set[str] = set()  # 防止一个 unit 被匹配两次

    for ext_unit in extracted_units:
        ext_type = ext_unit.get("unit_type", "")
        ext_name = _normalize(ext_unit.get("unit_name", ""))
        ext_sub_units: list[str] = [_normalize(s) for s in ext_unit.get("sub_units", [])]
        ext_confidence = ext_unit.get("confidence", "high")

        # ── 匹配 unit 本身 ────────────────────────────────
        # 如果 LLM 只抽取到「○○学部/○○研究科」但没有列出学科/専攻，
        # 则认为该 PDF 覆盖了 ground truth 中该 unit 下的所有子单元。
        # 这比 gt_index[name_only] 只命中第一条子单元更符合覆盖率语义。
        if not ext_sub_units:
            exact_group = _find_exact_unit_group(ext_name, known_units, ext_type)
            if exact_group:
                for ku in exact_group:
                    if ku["id"] not in matched_unit_ids:
                        matched_unit_ids.add(ku["id"])
                        results.append(_make_match(ku, ext_confidence, "exact"))
                continue

        # 先尝试 exact
        if ext_name in gt_index:
            ku = gt_index[ext_name]
            if ku["id"] not in matched_unit_ids:
                matched_unit_ids.add(ku["id"])
                results.append(_make_match(ku, ext_confidence, "exact"))
        else:
            # fuzzy 匹配
            best = _fuzzy_match(ext_name, gt_index)
            if best:
                ku, score = best
                if ku["id"] not in matched_unit_ids:
                    matched_unit_ids.add(ku["id"])
                    method = "fuzzy" if score >= FUZZY_THRESHOLD else "llm"
                    confidence = _score_to_confidence(score, ext_confidence)
                    results.append(_make_match(ku, confidence, method))

        # ── 匹配 sub_units（学科/専攻） ────────────────────
        for sub_name in ext_sub_units:
            composite_key = f"{ext_name}/{sub_name}"
            if composite_key in gt_index:
                ku = gt_index[composite_key]
                if ku["id"] not in matched_unit_ids:
                    matched_unit_ids.add(ku["id"])
                    results.append(_make_match(ku, ext_confidence, "exact"))
            else:
                # 在 ground truth 中找 unit_type 匹配且 sub_unit_name 最相似的
                best = _fuzzy_match_sub(ext_name, sub_name, known_units, ext_type)
                if best:
                    ku, score = best
                    if ku["id"] not in matched_unit_ids:
                        matched_unit_ids.add(ku["id"])
                        method = "fuzzy" if score >= FUZZY_THRESHOLD else "llm"
                        confidence = _score_to_confidence(score, ext_confidence)
                        results.append(_make_match(ku, confidence, method))

    return results


def _find_exact_unit_group(unit_name: str, known_units: list[dict], unit_type: str = "") -> list[dict]:
    """
    查找同一学部/研究科名下的所有 ground truth 记录。
    用于处理 PDF/LLM 只出现上位 unit 名、未展开学科/専攻的场景。
    """
    if not unit_name:
        return []

    matches = []
    for ku in known_units:
        if unit_type and ku.get("unit_type") != unit_type:
            continue
        if _normalize(ku.get("unit_name", "")) == unit_name:
            matches.append(ku)
    return matches


def _make_match(ku: dict, confidence: str, method: str) -> dict:
    unit_name = ku.get("unit_name", "")
    if ku.get("sub_unit_name"):
        unit_name += f"/{ku['sub_unit_name']}"
    return {
        "unit_id": ku["id"],
        "unit_name": unit_name,
        "confidence": confidence,
        "method": method,
    }


def _fuzzy_match(
    query: str,
    gt_index: dict[str, dict],
    threshold: int = FUZZY_MEDIUM,
) -> Optional[tuple[dict, float]]:
    """在 gt_index 的 keys 中寻找最相似的，返回 (record, score) 或 None"""
    if not HAS_RAPIDFUZZ or not query:
        return None

    candidates = list(gt_index.keys())
    result = rfprocess.extractOne(
        query, candidates,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
    )
    if result:
        best_key, score, _ = result
        return gt_index[best_key], float(score)
    return None


def _fuzzy_match_sub(
    unit_name: str,
    sub_name: str,
    known_units: list[dict],
    unit_type: str,
    threshold: int = FUZZY_MEDIUM,
) -> Optional[tuple[dict, float]]:
    """在 known_units 中找 unit_type 相同、sub_unit_name 最相似的记录"""
    if not HAS_RAPIDFUZZ or not sub_name:
        return None

    candidates = [
        ku for ku in known_units
        if ku.get("unit_type") == unit_type
        and ku.get("sub_unit_name")
    ]

    if not candidates:
        return None

    # 组合 unit_name/sub_unit_name 作为比较字符串
    candidate_keys = [
        f"{_normalize(ku['unit_name'])}/{_normalize(ku['sub_unit_name'])}"
        for ku in candidates
    ]
    query = f"{unit_name}/{sub_name}"

    result = rfprocess.extractOne(
        query, candidate_keys,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
    )
    if result:
        best_key, score, idx = result
        return candidates[idx], float(score)
    return None


def _score_to_confidence(score: float, llm_confidence: str) -> str:
    """将模糊匹配分数 + LLM 置信度转换为 high/medium/low"""
    if score >= FUZZY_THRESHOLD:
        return "high" if llm_confidence == "high" else "medium"
    elif score >= FUZZY_MEDIUM:
        return "medium" if llm_confidence != "low" else "low"
    else:
        return "low"