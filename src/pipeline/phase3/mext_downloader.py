"""
文部科学省 Excel ダウンローダー
https://www.mext.go.jp/a_menu/koutou/ichiran/mext_00038.html
のページから全国大学一覧 Excel を自動取得する。

使用方法:
    python -m src.pipeline.phase3.mext_downloader
    python -m src.pipeline.phase3.mext_downloader --output data/R06_daigaku.xlsx
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MEXT_INDEX_URL = "https://www.mext.go.jp/a_menu/koutou/ichiran/mext_00038.html"
MEXT_BASE_URL  = "https://www.mext.go.jp"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
}


def find_excel_url(page_html: str) -> str | None:
    """从文科省页面 HTML 中提取 Excel 下载链接"""
    soup = BeautifulSoup(page_html, "html.parser")

    # 搜索包含 .xlsx 或 .xls 的链接
    for a_tag in soup.find_all("a", href=True):
        href: str = a_tag["href"]
        if re.search(r"\.(xlsx|xls)$", href, re.IGNORECASE):
            # 处理相对路径
            if href.startswith("http"):
                return href
            elif href.startswith("/"):
                return MEXT_BASE_URL + href
            else:
                return MEXT_BASE_URL + "/" + href.lstrip("./")

    return None


def download_mext_excel(output_path: Path | None = None) -> Path:
    """
    从文部科学省官网下载全国大学一覧 Excel。

    Args:
        output_path: 保存路径（None 时自动命名到 data/ 目录）

    Returns:
        保存的文件路径
    """
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        # Step 1: 获取索引页
        logger.info("取得中: %s", MEXT_INDEX_URL)
        resp = client.get(MEXT_INDEX_URL, headers=HEADERS)
        resp.raise_for_status()

        # Step 2: 解析 Excel 链接
        excel_url = find_excel_url(resp.text)
        if not excel_url:
            raise RuntimeError(
                f"Excel リンクが見つかりませんでした: {MEXT_INDEX_URL}\n"
                "ページ構造が変わった可能性があります。手動で確認してください。"
            )
        logger.info("Excel URL: %s", excel_url)

        # Step 3: 下载 Excel
        logger.info("ダウンロード中...")
        dl_resp = client.get(excel_url, headers=HEADERS)
        dl_resp.raise_for_status()

        # Step 4: 保存
        if output_path is None:
            data_dir = Path("data")
            data_dir.mkdir(exist_ok=True)
            filename = excel_url.split("/")[-1] or "daigaku_ichiran.xlsx"
            output_path = data_dir / filename

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(dl_resp.content)

        logger.info("保存完了: %s (%.1f KB)", output_path, len(dl_resp.content) / 1024)
        return output_path


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s]: %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="文部科学省 Excel ダウンロード")
    parser.add_argument("--output", help="保存先パス（例: data/R06.xlsx）")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else None
    try:
        saved = download_mext_excel(output_path)
        print(f"✓ ダウンロード成功: {saved}")
    except Exception as e:
        logger.error("失敗: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()