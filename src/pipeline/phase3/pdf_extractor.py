"""
PDF 内容提取器
使用 pdfplumber 提取文字，使用 LLM 结构化提取募集要項中的学部/研究科/専攻信息。
"""
from __future__ import annotations

import io
import json
import logging
import re
from typing import Optional

import pdfplumber

logger = logging.getLogger(__name__)

# PDF 文本最大字符数（超长 PDF 截断，防止超出 LLM context window）
MAX_TEXT_CHARS = 12_000
# 只取前 N 页做 scope 判断（节省 token）
SCOPE_DETECTION_PAGES = 5


def extract_text_from_bytes(raw_bytes: bytes, max_pages: Optional[int] = None) -> str:
    """
    从 PDF 字节流中提取纯文本。

    Args:
        raw_bytes: PDF 原始字节
        max_pages: 最多提取页数（None = 全部）

    Returns:
        合并后的文本字符串
    """
    texts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            pages = pdf.pages if max_pages is None else pdf.pages[:max_pages]
            for page in pages:
                text = page.extract_text()
                if text:
                    texts.append(text)
    except Exception as e:
        logger.error("pdfplumber 提取失败: %s", e)
        return ""

    return "\n".join(texts)


def detect_pdf_scope(text: str) -> str:
    """
    根据 PDF 文本推断 scope：
      - undergraduate: 只有学部
      - graduate: 只有研究科/大学院
      - combined: 两者都有
    """
    has_undergrad = bool(re.search(r"学部|学科|学群|学類", text))
    has_grad = bool(re.search(r"研究科|大学院|専攻|修士|博士", text))

    if has_undergrad and has_grad:
        return "combined"
    elif has_grad:
        return "graduate"
    elif has_undergrad:
        return "undergraduate"
    else:
        return "combined"  # 不确定时默认 combined


def build_extraction_prompt(
    university_name: str,
    pdf_text: str,
    known_units: Optional[list[dict]] = None,
) -> str:
    """
    构建 LLM 提取 Prompt。

    Args:
        university_name: 大学名（提供上下文）
        pdf_text: PDF 提取的文本
        known_units: ground truth 中已知的 unit 列表（辅助 LLM 对齐）
    """
    known_units_str = ""
    if known_units:
        units_preview = known_units[:20]  # 最多列20个
        known_units_str = "\n\n【参考：文部科学省データベースに登録されている学部・研究科】\n"
        for u in units_preview:
            line = f"  - [{u['unit_type']}] {u['unit_name']}"
            if u.get("sub_unit_name"):
                line += f" / {u['sub_unit_name']}"
            known_units_str += line + "\n"
        if len(known_units) > 20:
            known_units_str += f"  ... 他 {len(known_units) - 20} 件\n"

    prompt = f"""あなたは大学の募集要項PDFから情報を抽出する専門家です。
以下のPDFテキストから、この大学の学部・研究科・学科・専攻の情報を構造化して抽出してください。

【大学名】{university_name}
{known_units_str}

【PDFテキスト（抜粋）】
{pdf_text[:MAX_TEXT_CHARS]}

【抽出ルール】
1. 学部名・研究科名・学科名・専攻名を正確に抽出すること
2. 「研究科」「大学院」を含む場合は unit_type を "研究科" とする
3. 「学部」「学群」「学院」「学類」を含む場合は unit_type を "学部" とする
4. sub_units には学科名（学部の場合）または専攻名（研究科の場合）を列挙する
5. このPDFが学部のみ・大学院のみ・両方を含むかを pdf_scope で示す
6. 確信度が低い場合は confidence を "low" または "medium" にする

【出力形式（JSONのみ、説明文不要）】
{{
  "university_name": "{university_name}",
  "academic_year": "令和7年度",
  "pdf_scope": "undergraduate | graduate | combined",
  "covered_units": [
    {{
      "unit_type": "学部 | 研究科",
      "unit_name": "○○学部",
      "sub_units": ["○○学科", "△△学科"],
      "confidence": "high | medium | low"
    }}
  ],
  "notes": "補足事項があれば記載"
}}"""
    return prompt


def parse_llm_extraction_result(llm_response: str) -> Optional[dict]:
    """
    解析 LLM 返回的 JSON 结果。
    支持 LLM 在 JSON 前后附带说明文字的情况。
    """
    # 尝试提取 JSON 块
    json_match = re.search(r"\{.*\}", llm_response, re.DOTALL)
    if not json_match:
        logger.warning("LLM 返回中未找到 JSON: %s...", llm_response[:200])
        return None

    try:
        result = json.loads(json_match.group())
        return result
    except json.JSONDecodeError as e:
        logger.warning("JSON 解析失败: %s\n原文: %s...", e, llm_response[:200])
        return None