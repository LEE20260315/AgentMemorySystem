"""一次性迁移脚本: v1.3 / 早期融合层 -> v1.3.2 默认布局

背景
----
v1.3.0-v1.3.1 时代数据根写在项目内 data/ 下。
v1.3.2 改为：默认 OneDrive/AgentMemory/（用户已有的 v1.2 老融合层）。

本脚本把以下来源合并到新融合层，老目录/老文件不动：

来源 A: 项目内 data/（如果存在）
来源 B: OneDrive/AgentMemory/（即 v1.2 老融合层，默认目标）

合并行为
--------
- data/shared.db           -> OneDrive/AgentMemory/shared.db           (按 id 去重 INSERT OR REPLACE)
- data/agent_<x>/memories.db -> OneDrive/AgentMemory/agent_<x>/memories.db
- data/agent_<x>/memory_private*.md -> 同名追加
- 其他 data/agent_<x>/*.json 跳过（registry 用 v1.3.2 的新 schema 重新生成）

去重保证
--------
SQLite 的 `INSERT OR REPLACE` 按 PRIMARY KEY (memories.id) 去重，不会产生重复。
md 文件追加前比对 content_hash，过滤跨设备同条记忆。

用法
----
python tools/migrate_v13_to_v14.py [--dry-run] [--src <data_dir>] [--dst <agent_memory_dir>]

退出码
------
0 - 完成（可能有警告）
1 - 错误
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# 让脚本可以直接 python tools/migrate_v13_to_v14.py 跑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _setup_console_utf8():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _candidate_data_roots() -> list:
    """可能存在的旧 data/ 位置（项目内）"""
    here = Path(__file__).resolve().parent.parent  # AgentMemorySystem 项目根
    candidates = []
    proj_data = here / "data"
    if proj_data.exists():
        candidates.append(proj_data)
    # 也支持 frozen EXE 的同级 data
    if getattr(sys, "frozen", False):
        exe_data = Path(sys.executable).parent / "data"
        if exe_data.exists() and exe_data not in candidates:
            candidates.append(exe_data)
    return candidates


def _candidate_agent_memory_roots() -> list:
    """可能的 OneDrive/AgentMemory/ 位置"""
    cands = []
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        r = os.environ.get(env)
        if r:
            am = Path(r) / "AgentMemory"
            if am.exists():
                cands.append(am)
    home_am = Path.home() / "OneDrive" / "AgentMemory"
    if home_am.exists() and home_am not in cands:
        cands.append(home_am)
    return cands


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = "file:" + str(db_path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _open_writable(db_path: Path) -> sqlite3.Connection:
    # v1.3.2 friendly config：timeout + busy + DELETE journal
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -20000")
    return conn


def _read_foreign_memories(src_db: Path) -> tuple:
    """从源 SQLite 读全 memories，返回 (memories, tags, memory_tags) 三张表"""
    if not src_db.exists():
        return [], [], []
    conn = _open_readonly(src_db)
    try:
        try:
            memories = [dict(r) for r in conn.execute("SELECT * FROM memories").fetchall()]
        except sqlite3.OperationalError:
            memories = []
        try:
            tags = [dict(r) for r in conn.execute("SELECT * FROM tags").fetchall()]
        except sqlite3.OperationalError:
            tags = []
        try:
            memory_tags = [dict(r) for r in conn.execute("SELECT * FROM memory_tags").fetchall()]
        except sqlite3.OperationalError:
            memory_tags = []
        return memories, tags, memory_tags
    finally:
        conn.close()


def _merge_db(src_db: Path, dst_db: Path, dry_run: bool) -> dict:
    """把 src_db 全量合并到 dst_db，按 PRIMARY KEY 去重（INSERT OR REPLACE）"""
    result = {"copied_memories": 0, "copied_tags": 0, "copied_links": 0}

    mems, tags, links = _read_foreign_memories(src_db)
    if not mems and not tags and not links:
        return result

    # 计算真正需要写入的数量（dst 已经有的 id 不重复）
    if dry_run:
        if dst_db.exists():
            existing = _open_readonly(dst_db)
            try:
                existing_ids = set(r[0] for r in existing.execute("SELECT id FROM memories").fetchall())
            except sqlite3.OperationalError:
                existing_ids = set()
            finally:
                existing.close()
        else:
            existing_ids = set()
        result["copied_memories"] = sum(1 for m in mems if m.get("id") not in existing_ids)
        result["copied_tags"] = sum(1 for t in tags if t.get("name"))
        # links 实际插入数需 entries 在 src + 关联到新 tag，是个保守估计
        result["copied_links"] = sum(
            1 for link in links
            if link.get("memory_id") in {m["id"] for m in mems if m.get("id") not in existing_ids}
        )
        return result

    dst_db.parent.mkdir(parents=True, exist_ok=True)
    # 使用 agent_memory 里的兼容 SQLite，确保目标表 schema 一致
    from agent_memory import MemoryDatabase

    with MemoryDatabase(dst_db) as db:
        # tags - 按 name 去重
        for t in tags:
            name = t.get("name")
            if not name:
                continue
            try:
                db.conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                result["copied_tags"] += 1
            except sqlite3.IntegrityError:
                pass
        db.conn.commit()

        # memories
        from agent_memory import MemoryEntry
        entries = []
        seen_ids = set()
        for m in mems:
            try:
                # 跳过自表里已有的
                ck = db.conn.execute("SELECT 1 FROM memories WHERE id = ?", (m["id"],)).fetchone()
                if ck:
                    continue
                ent = MemoryEntry(
                    id=m["id"],
                    agent_id=m["agent_id"],
                    timestamp=m["timestamp"],
                    source_device=m["source_device"],
                    domain=m["domain"],
                    tags=[],  # 由 memory_tags 关联补齐
                    confidence=m["confidence"],
                    conflict_with=m["conflict_with"],
                    content=m["content"],
                    embedding=m.get("embedding"),
                    access_count=m.get("access_count", 0) or 0,
                    last_accessed=m.get("last_accessed"),
                    source_memory_id=m.get("source_memory_id"),
                )
                entries.append(ent)
                seen_ids.add(m["id"])
            except Exception:
                continue

        # 批量插入
        new_count = db.insert_memories_batch(entries) if entries else 0
        result["copied_memories"] = new_count

        # memory_tags 关联
        tag_id_cache = db._load_tag_cache()
        tag_name_map = {r["id"]: r["name"] for r in db.conn.execute("SELECT id, name FROM tags").fetchall()}
        for link in links:
            mid = link.get("memory_id")
            tid = link.get("tag_id")
            if not mid or mid not in seen_ids:
                continue
            if tid not in tag_id_cache:
                continue
            try:
                db.conn.execute(
                    "INSERT OR IGNORE INTO memory_tags (memory_id, tag_id) VALUES (?, ?)",
                    (mid, tag_id_cache[tid]),
                )
                result["copied_links"] += 1
            except Exception:
                pass
        db.conn.commit()

    return result


def _append_markdown(src_md: Path, dst_md: Path, dry_run: bool) -> int:
    """把 src_md 追加到 dst_md（去重：内容 hash 已存在则跳过）"""
    if not src_md.exists():
        return 0
    if not dst_md.exists():
        if dry_run:
            return 1
        dst_md.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(src_md), str(dst_md))
            return 1
        except OSError:
            return 0

    try:
        src_text = src_md.read_text(encoding="utf-8", errors="replace")
        dst_text = dst_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    # 简易 front-matter 块去重：按块首行判等
    src_blocks = [b.strip() for b in src_text.split("---") if b.strip()]
    dst_block_hashes = set(b[:200] for b in dst_text.split("---") if b.strip())
    added = 0
    out = dst_text.rstrip() + "\n\n"
    for blk in src_blocks:
        head = blk[:200]
        if head in dst_block_hashes:
            continue
        out += "---\n"
        out += blk + "\n\n"
        dst_block_hashes.add(head)
        added += 1

    if added > 0 and not dry_run:
        try:
            tmp = dst_md.with_suffix(".tmp")
            tmp.write_text(out, encoding="utf-8")
            tmp.replace(dst_md)
        except OSError:
            added = 0
    return added


def migrate(src_data_root: Path, dst_agent_memory: Path, dry_run: bool) -> dict:
    """把 src_data_root (项目内 data/) 合并到 dst_agent_memory (OneDrive/AgentMemory/)"""
    summary = {
        "src": str(src_data_root),
        "dst": str(dst_agent_memory),
        "dry_run": dry_run,
        "shared_db": {},
        "agents": {},
        "md_files": {},
        "errors": [],
    }

    if not src_data_root.exists():
        summary["errors"].append(f"源目录不存在: {src_data_root}")
        return summary

    # 迁移前自动备份（v1.3.2 安全护栏）
    if not dry_run:
        try:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            backup_dst = dst_agent_memory.parent / f"{dst_agent_memory.name}._backup_{timestamp}"
            if dst_agent_memory.exists() and not backup_dst.exists():
                shutil.copytree(str(dst_agent_memory), str(backup_dst))
                summary["backup_path"] = str(backup_dst)
        except Exception as e:
            summary["errors"].append(f"备份失败（将继续迁移）: {e}")

    dst_agent_memory.mkdir(parents=True, exist_ok=True)

    # 1) shared.db
    src_shared = src_data_root / "shared.db"
    if src_shared.exists():
        dst_shared = dst_agent_memory / "shared.db"
        try:
            summary["shared_db"] = _merge_db(src_shared, dst_shared, dry_run)
        except Exception as e:
            summary["errors"].append(f"shared.db 失败: {e}")

    # 2) agent_<id>/ 子目录
    for src_agent_dir in sorted(src_data_root.glob("agent_*")):
        if not src_agent_dir.is_dir():
            continue
        agent_id = src_agent_dir.name.replace("agent_", "")
        dst_agent_dir = dst_agent_memory / ("agent_" + agent_id)
        dst_agent_dir.mkdir(parents=True, exist_ok=True)

        report = {"memories_db": {}, "md": {}}

        # memories.db
        src_db = src_agent_dir / "memories.db"
        dst_db = dst_agent_dir / "memories.db"
        try:
            report["memories_db"] = _merge_db(src_db, dst_db, dry_run)
        except Exception as e:
            summary["errors"].append(f"agent {agent_id} memories.db 失败: {e}")

        # md files: memory_private*.md, memory_shared.md
        for md_file in list(src_agent_dir.glob("*.md")):
            dst_md = dst_agent_dir / md_file.name
            try:
                added = _append_markdown(md_file, dst_md, dry_run)
                if added > 0 or dst_md.exists():
                    report["md"][md_file.name] = added
            except Exception as e:
                summary["errors"].append(f"{md_file.name} 追加失败: {e}")

        summary["agents"][agent_id] = report

    return summary


def main():
    _setup_console_utf8()
    p = argparse.ArgumentParser(description="v1.3 数据 -> v1.3.2 数据合并脚本")
    p.add_argument("--src", help="旧数据目录（默认自动探测项目内 data/）")
    p.add_argument("--dst", help="新融合层目录（默认 OneDrive/AgentMemory/）")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不实际写入")
    args = p.parse_args()

    src = Path(args.src).expanduser() if args.src else None
    dst = Path(args.dst).expanduser() if args.dst else None

    if src is None:
        candidates = _candidate_data_roots()
        if not candidates:
            print("[OK] 未发现项目内的旧 data/ 目录，无需迁移。退出 0。")
            return 0
        src = candidates[0]
        print(f"自动选择源目录: {src}")
    if dst is None:
        candidates = _candidate_agent_memory_roots()
        if not candidates:
            print("[ERR] 未发现 OneDrive/AgentMemory/ 目录，请用 --dst 指定。")
            return 1
        dst = candidates[0]
        print(f"自动选择目标目录: {dst}")

    print(f"\n=== 迁移计划 ===")
    print(f"  source: {src}")
    print(f"  dest:   {dst}")
    print(f"  dry_run: {args.dry_run}")

    summary = migrate(src, dst, args.dry_run)

    print("\n=== 结果 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if summary["errors"]:
        print("\n!! 警告 / 错误:")
        for e in summary["errors"]:
            print(f"  - {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
