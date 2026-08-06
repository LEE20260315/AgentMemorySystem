#!/usr/bin/env python3
"""修复 SQLite FTS 索引孤儿条目 + 回收空间（v2.1.0）

背景：
    历史版本中删除 memories 行时从未同步删除 memories_fts 行，
    导致 FTS 索引只增不减：memories 仅 53-68 行但 memories_fts 达
    1292-2422 行（90%+ 孤儿），数据库膨胀到 8-14MB。

本工具对数据根目录下所有 *.db 执行：
  1. 删除 FTS 孤儿（id 不在 memories 中）
  2. optimize 整理 FTS 分片
  3. VACUUM 回收物理空间

用法：
    python tools/repair_fts.py [--root <数据根>] [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def repair_db(db_path: Path, dry_run: bool = False) -> dict:
    """修复单个数据库的 FTS 索引。"""
    result = {"db": str(db_path), "memories": 0, "fts_before": 0,
              "fts_after": 0, "orphans": 0, "size_before": 0, "size_after": 0}
    try:
        conn = sqlite3.connect(str(db_path), timeout=60)
        conn.execute("PRAGMA busy_timeout = 60000")
        result["size_before"] = db_path.stat().st_size if db_path.exists() else 0

        # 表存在性检查
        has_fts = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='memories_fts'").fetchone()
        has_mem = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='memories'").fetchone()
        if not has_fts or not has_mem:
            conn.close()
            result["skipped"] = True
            return result

        result["memories"] = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        result["fts_before"] = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]

        # 孤儿 = fts 中存在但 memories 不存在
        orphans = conn.execute(
            """SELECT COUNT(*) FROM memories_fts
               WHERE id NOT IN (SELECT id FROM memories)"""
        ).fetchone()[0]
        result["orphans"] = orphans

        if not dry_run and orphans > 0:
            conn.execute("""DELETE FROM memories_fts
                            WHERE id NOT IN (SELECT id FROM memories)""")
            conn.commit()
            # 整理 FTS 碎片
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('optimize')")
            conn.commit()
            # VACUUM 回收空间（VACUUM 不能在事务中）
            conn.execute("VACUUM")

        result["fts_after"] = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        result["size_after"] = db_path.stat().st_size if db_path.exists() else 0
        conn.close()
    except Exception as e:
        result["error"] = str(e)
    return result


def main():
    ap = argparse.ArgumentParser(description="修复 FTS 索引孤儿")
    ap.add_argument("--root", default=None, help="数据根目录（默认 get_data_root）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不修复")
    args = ap.parse_args()

    if args.root:
        root = Path(args.root)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from safe_io import get_data_root
        root = get_data_root()

    dbs = sorted(root.glob("*.db")) + sorted(root.glob("agent_*/memories.db"))
    if not dbs:
        print("未找到数据库文件: {}".format(root))
        return 1

    total_before = total_after = 0
    total_orphans = 0
    print("数据根: {}".format(root))
    print("{} 数据库待检查\n".format(len(dbs)))
    for db_path in dbs:
        r = repair_db(db_path, args.dry_run)
        if r.get("skipped"):
            print("[skip] {} （无 memories 表）".format(r["db"]))
            continue
        if r.get("error"):
            print("[error] {}: {}".format(r["db"], r["error"]))
            continue
        total_before += r["size_before"]
        total_after += r["size_after"]
        total_orphans += r["orphans"]
        tag = "DRY-RUN" if args.dry_run else "OK"
        size_delta = (r["size_after"] - r["size_before"]) / 1024 / 1024
        print("[{}] {} memories={} fts: {}->{} orphans={} size: {:.2f}MB->{:.2f}MB ({:+.2f}MB)".format(
            tag, r["db"], r["memories"], r["fts_before"], r["fts_after"],
            r["orphans"], r["size_before"] / 1024 / 1024,
            r["size_after"] / 1024 / 1024, size_delta))

    print("\n汇总: 孤儿 {} 条, 空间 {:.2f}MB -> {:.2f}MB (回收 {:.2f}MB)".format(
        total_orphans, total_before / 1024 / 1024, total_after / 1024 / 1024,
        (total_before - total_after) / 1024 / 1024))
    if args.dry_run:
        print("（dry-run 模式，未实际修复。去掉 --dry-run 执行修复）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
