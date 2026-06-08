"""
PDF タイトル抽取 — PDF の第1ページから大見出しを取得し、
リンクテキストの代わりに使用する。

処理フロー:
  1. PDF をダウンロード（キャッシュあり）
  2. pypdf で第1ページのテキストを抽出
  3. 最初の意味のある行を「タイトル」として返す
  4. 失敗時は元のリンクテキストをそのまま使用
"""

import hashlib
import io
import logging
import re
from pathlib import Path

import requests
import pypdf

from config import REQUEST_TIMEOUT, CACHE_DIR

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AdmissionCrawler/1.0; research)"}

# PDF キャッシュディレクトリ（ページキャッシュとは別）
PDF_CACHE_DIR = CACHE_DIR.parent / "_pdf_title_cache"
PDF_CACHE_DIR.mkdir(exist_ok=True)

# タイトルとして使わない短すぎる・意味のない行のパターン
_NOISE_PATTERNS = [
    r"^\s*$",                    # 空行
    r"^\d+$",                    # 数字のみ（ページ番号）
    r"^[A-Za-z]{1,2}$",          # 1〜2文字のアルファベット
    r"^\s*[-―─=＝]+\s*$",        # 区切り線
]
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS))

# タイトルとして有効な最小文字数
MIN_TITLE_LEN = 4


def _pdf_cache_path(url: str) -> Path:
    key = hashlib.md5(url.encode()).hexdigest()
    return PDF_CACHE_DIR / f"{key}.txt"


def _load_title_cache(url: str) -> str | None:
    path = _pdf_cache_path(url)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _save_title_cache(url: str, title: str) -> None:
    _pdf_cache_path(url).write_text(title, encoding="utf-8")


def _clean_text(text: str) -> str:
    """PDF から抽出したテキストのノイズ除去"""
    # 不要な空白・改行を整理
    text = re.sub(r"\s+", " ", text).strip()
    # 全角スペースを半角スペースに
    text = re.sub(r"[　\u3000]+", " ", text)
    # Windows cp932 で表示できない特殊文字を除去（CJK互換・異体字セレクタ等）
    text = re.sub(r"[\u2e80-\u2eff\u2f00-\u2fdf\ufe30-\ufe4f]", "", text)
    # 制御文字を除去
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text.strip()


def _extract_title_from_text(raw_text: str) -> str:
    """
    PDF 第1ページのテキストから大見出し（タイトル）を抽出する。

    戦略:
    1. 行に分割
    2. ノイズ行をスキップ
    3. 最初の意味のある行を最大 80 文字で返す
    4. 短い行が連続する場合は最大 3 行を結合（「令和8年度\n募集要項」など）
    """
    lines = [_clean_text(ln) for ln in raw_text.split("\n")]
    lines = [ln for ln in lines if ln and not _NOISE_RE.match(ln)]

    if not lines:
        return ""

    # 最初の意味のある行を取得
    title_parts = []
    for line in lines[:5]:  # 最大5行まで確認
        if len(line) >= MIN_TITLE_LEN:
            title_parts.append(line)
            # 合計が十分長くなったら終了
            if len("".join(title_parts)) >= 15:
                break

    if not title_parts:
        return ""

    # 短い行が複数ある場合は結合（最大3行、80文字まで）
    title = " ".join(title_parts[:3])[:80]
    return title.strip()


def fetch_pdf_title(url: str) -> str:
    """
    PDF URL からタイトルを取得する。

    優先順位:
    1. キャッシュ
    2. PDF メタデータの /Title フィールド
    3. 第1ページの先頭テキスト
    4. 失敗時は空文字列を返す
    """
    # キャッシュチェック
    cached = _load_title_cache(url)
    if cached is not None:
        logger.debug(f"PDF title cache hit: {url}")
        return cached

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True)
        if resp.status_code != 200:
            logger.warning(f"PDF fetch failed ({resp.status_code}): {url}")
            return ""

        # サイズ制限（最大 5MB）
        content = b""
        for chunk in resp.iter_content(chunk_size=65536):
            content += chunk
            if len(content) > 5 * 1024 * 1024:
                logger.debug(f"PDF too large, truncating: {url}")
                break

        reader = pypdf.PdfReader(io.BytesIO(content), strict=False)

        # 方法1: メタデータの /Title
        meta_title = ""
        if reader.metadata and reader.metadata.title:
            meta_title = _clean_text(reader.metadata.title)
            # 16進数ゴミ値・英数字のみの短いタイトルを除外
            is_hex_garbage = bool(re.match(r'^[0-9A-Fa-f]{10,}$', meta_title.replace(' ', '')))
            has_cjk = bool(re.search(r'[\u3000-\u9fff\uF900-\uFAFF]', meta_title))
            has_latin_words = bool(re.search(r'[A-Za-z]{3,}', meta_title))
            if len(meta_title) >= MIN_TITLE_LEN and not is_hex_garbage and (has_cjk or has_latin_words):
                logger.debug(f"PDF meta title: {meta_title[:50]}")
                _save_title_cache(url, meta_title)
                return meta_title

        # 方法2: 第1ページのテキスト
        if reader.pages:
            first_page_text = reader.pages[0].extract_text() or ""
            page_title = _extract_title_from_text(first_page_text)
            if page_title:
                logger.debug(f"PDF page title: {page_title[:50]}")
                _save_title_cache(url, page_title)
                return page_title

        # どちらも失敗
        logger.debug(f"Could not extract title from PDF: {url}")
        _save_title_cache(url, "")
        return ""

    except Exception as e:
        logger.warning(f"PDF title extraction failed: {url} — {e}")
        return ""


def _is_better_title(new_title: str, original_text: str) -> bool:
    """
    PDF から抽出したタイトルが元のリンクテキストより良いかどうかを判断する。
    """
    if not new_title:
        return False

    # Microsoft Word ファイル名パターンを除外
    if new_title.startswith("Microsoft Word") or new_title.startswith("Microsoft Excel"):
        return False

    # ファイル名だけのパターンを除外（拡張子なしでも英数字+アンダースコアのみ）
    if re.match(r'^[A-Za-z0-9_\-]+$', new_title) and len(new_title) < 30:
        return False

    # 元のテキストより明らかに短い場合は採用しない
    if len(new_title) < len(original_text) * 0.5 and len(original_text) > 10:
        return False

    # CJK文字または意味のある英単語を含む場合は採用
    has_cjk = bool(re.search(r'[\u3040-\u9fff]', new_title))
    has_meaningful_latin = bool(re.search(r'[A-Za-z]{4,}', new_title))
    return has_cjk or has_meaningful_latin


def enrich_pdf_list(pdfs: list[dict], min_text_len: int = 0) -> list[dict]:
    """
    PDF リストの text フィールドを PDF 内タイトルで補完・強化する。

    min_text_len: この文字数未満の text のみ補完する場合は 0 以外を指定。
                  0 = 全件対象（方案B: 全量抽取）

    Returns: 強化済みの PDF リスト（url は変更なし）
    """
    enriched = []
    for pdf in pdfs:
        url = pdf.get("url", "")
        original_text = pdf.get("text", "")

        # 全量抽取（min_text_len=0）または短いテキストのみ対象
        should_enrich = (min_text_len == 0) or (len(original_text) < min_text_len)

        if should_enrich and url:
            pdf_title = fetch_pdf_title(url)
            if _is_better_title(pdf_title, original_text):
                new_text = pdf_title
                logger.info(f"Enriched PDF text: '{original_text[:30]}' → '{pdf_title[:50]}'")
            else:
                new_text = original_text  # 失敗または品質不十分の場合は元のテキストを維持
                if pdf_title:
                    logger.debug(f"Kept original text (PDF title not better): '{original_text[:30]}'")
        else:
            new_text = original_text

        enriched.append({
            "url": url,
            "text": new_text,
            "original_text": original_text,  # 元のリンクテキストも保持
        })

    return enriched