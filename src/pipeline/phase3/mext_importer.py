"""
文部科学省 Excel → Supabase university_units 批量导入器

使用方法:
    python -m src.pipeline.phase3.mext_importer \
        --excel data/R06_daigaku_ichiran.xlsx \
        --dry-run          # 仅打印，不写库
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.pipeline.phase3.mext_excel_parser import MextExcelParser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def import_excel_to_db(
    excel_path: str | Path,
    dry_run: bool = False,
    batch_size: int = 100,
) -> dict:
    """
    解析 Excel 并批量导入 university_units 表。

    Args:
        excel_path: 文部科学省 Excel 文件路径
        dry_run: True 时只打印，不实际写数据库
        batch_size: 每批提交数量（用于进度日志）

    Returns:
        {"total": int, "inserted": int, "updated": int, "errors": int}
    """
    parser = MextExcelParser(excel_path)
    records = parser.parse()

    logger.info("解析完毕，共 %d 条记录，开始导入...", len(records))

    stats = {"total": len(records), "inserted": 0, "updated": 0, "errors": 0}

    if dry_run:
        logger.info("[DRY-RUN] 前10条预览:")
        for r in records[:10]:
            logger.info("  %s", r)
        logger.info("[DRY-RUN] 共 %d 条，未写入数据库", len(records))
        return stats

    from src.db.operations import upsert_university_unit

    for i, rec in enumerate(records, 1):
        try:
            result = upsert_university_unit(
                university_name=rec["university_name"],
                unit_type=rec["unit_type"],
                unit_name=rec["unit_name"],
                sub_unit_name=rec.get("sub_unit_name"),
                prefecture=rec.get("prefecture"),
            )
            if result["action"] == "inserted":
                stats["inserted"] += 1
            else:
                stats["updated"] += 1
        except Exception as e:
            logger.error("导入失败 [%d/%d]: %s — %s", i, len(records), rec, e)
            stats["errors"] += 1

        if i % batch_size == 0:
            logger.info(
                "进度: %d/%d (插入=%d, 更新=%d, 错误=%d)",
                i, len(records),
                stats["inserted"], stats["updated"], stats["errors"],
            )

    logger.info(
        "导入完成 ✓ 总计=%d | 新插入=%d | 更新=%d | 错误=%d",
        stats["total"], stats["inserted"], stats["updated"], stats["errors"],
    )
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="文部科学省 Excel → university_units 批量导入"
    )
    parser.add_argument(
        "--excel", required=True,
        help="Excel 文件路径，例如: data/R06_daigaku_ichiran.xlsx"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅解析打印，不写数据库"
    )
    parser.add_argument(
        "--batch-size", type=int, default=100,
        help="批量提交大小（默认100）"
    )
    args = parser.parse_args()

    excel_path = Path(args.excel)
    if not excel_path.exists():
        logger.error("文件不存在: %s", excel_path)
        sys.exit(1)

    import_excel_to_db(
        excel_path=excel_path,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()