#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一次性清理工具：批量清除 Agent 本地记忆文件中的 legacy [sync:<id>] 标记。

背景
----
v2.0 之前的 sync 标记格式为 [sync:<id>]（无 h: 字段）。
reconcile_with_target_hashes() 对这类 legacy marker 走保守模式，
导致 SyncState 永远不被清理（写回死锁）。

本工具一次性扫描所有 Agent 的本地记忆文件，把 legacy marker 升级为
新格式 [sync:<id>|h:<content_hash>|src:<agent_id>]，或直接移除
（当无法重建 hash 时）。

用法
----
    # 预览（不写入）
    python tools/clean_legacy_markers.py --dry-run

    # 执行清理
    python tools/clean_legacy_markers.py

    # 指定根目录
    python tools/clean_legacy_markers.py --root /path/to/AgentMemorySystem

    # 只清理单个 agent
    python tools/clean_legacy_markers.py --agent hermes

通用性
------
不含任何硬编码路径，默认 root 为脚本上级目录。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import shutil
from pathlib import Path
from datetime import datetime


# legacy marker 正则：[sync:<id>] 但不含 |h: 字段
LEGACY_MARKER_RE = re.compile(r"\[sync:(?![^\]]*\|h:)[^\]]+\]")


def find_candidate_files(root: Path) -> list:
    """扫描 data/agent_*/ 和 data/_shared/ 下的候选记忆文件。

    候选文件名与 sync_writers.BaseMemoryWriter._candidate_filenames() 保持一致。
    """
    candidates = []
    candidate_names = [
        "MEMORY.md", "memory.md", "memories.md",
        "user_profile.md", "shared.md", "memory_shared.md",
        "shared_from_agents.md",
    ]
    data_dir = root / "data"
    if not data_dir.exists():
        return candidates

    for agent_dir in data_dir.iterdir():
        if not agent_dir.is_dir():
            continue
        for name in candidate_names:
            f = agent_dir / name
            if f.exists() and f.is_file():
                candidates.append(f)
    return candidates


def find_agent_local_files(root: Path, agent_filter: str = None) -> list:
    """扫描 Agent 本地安装目录下的记忆文件（可选）。

    通过 detect_agents() 发现的路径来扫描。
    """
    candidates = []
    try:
        sys.path.insert(0, str(root))
        from agent_memory import detect_agents
        detected = detect_agents(force_redetect=True, write_cache=False)
        for agent_id, info in detected.items():
            if agent_filter and agent_filter != agent_id:
                continue
            for f in info.get("memory_files", []):
                p = Path(f)
                # 跳过带 ?oversize=true 标记的
                if "?oversize=" in str(p):
                    continue
                if p.exists() and p.is_file() and p.suffix in (".md",):
                    candidates.append(p)
    except Exception as e:
        print("[WARN] 扫描 Agent 本地文件失败: {}".format(e))
    return candidates


def clean_file(file_path: Path, dry_run: bool = False) -> dict:
    """清理单个文件中的 legacy marker。

    Returns
    -------
    dict
        {file, legacy_count, cleaned, backed_up, error}
    """
    result = {
        "file": str(file_path),
        "legacy_count": 0,
        "cleaned": 0,
        "backed_up": False,
        "error": None,
    }
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        result["error"] = "读取失败: {}".format(e)
        return result

    matches = LEGACY_MARKER_RE.findall(text)
    result["legacy_count"] = len(matches)
    if not matches:
        return result

    # 直接移除 legacy marker（无法重建 hash，因为不知道原始 content）
    # 移除后清理残留的空列表项
    cleaned_text = LEGACY_MARKER_RE.sub("", text)
    # 清理脱敏后残留的空列表项 "- "
    cleaned_text = re.sub(r"(?m)^[ \t]*-[ \t]*$", "", cleaned_text)
    # 压缩连续空格
    cleaned_text = re.sub(r"[ \t]{2,}", " ", cleaned_text)
    # 压缩连续空行
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)
    cleaned_text = cleaned_text.strip() + "\n"

    result["cleaned"] = len(matches)

    if dry_run:
        return result

    # 备份后写入
    backup_path = file_path.with_suffix(file_path.suffix + ".bak_legacy_clean")
    try:
        shutil.copy2(str(file_path), str(backup_path))
        result["backed_up"] = True
    except Exception as e:
        result["error"] = "备份失败: {}".format(e)
        return result

    try:
        file_path.write_text(cleaned_text, encoding="utf-8")
    except Exception as e:
        result["error"] = "写入失败: {}".format(e)
        return result

    return result


def main():
    parser = argparse.ArgumentParser(
        description="清理 Agent 记忆文件中的 legacy [sync:<id>] 标记"
    )
    parser.add_argument(
        "--root",
        default=None,
        help="AgentMemorySystem 根目录（默认为脚本上级目录）",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="只清理指定 Agent（默认全部）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式，不实际写入",
    )
    parser.add_argument(
        "--scan-local",
        action="store_true",
        help="同时扫描 Agent 本地安装目录（~/.hermes/ 等）",
    )
    args = parser.parse_args()

    # 确定 root
    if args.root:
        root = Path(args.root).resolve()
    else:
        root = Path(__file__).resolve().parent.parent

    print("=" * 60)
    print("Legacy Sync Marker 清理工具 v1.0")
    print("=" * 60)
    print("根目录: {}".format(root))
    print("模式: {}".format("预览 (dry-run)" if args.dry_run else "执行"))
    if args.agent:
        print("Agent 过滤: {}".format(args.agent))
    print()

    # 收集候选文件
    files = find_candidate_files(root)
    if args.scan_local:
        files.extend(find_agent_local_files(root, args.agent))
    # 去重
    seen = set()
    unique_files = []
    for f in files:
        key = str(f.resolve())
        if key not in seen:
            seen.add(key)
            unique_files.append(f)
    files = unique_files

    if not files:
        print("[INFO] 未找到候选文件")
        return 0

    print("扫描 {} 个候选文件...".format(len(files)))
    print()

    total_legacy = 0
    total_cleaned = 0
    total_errors = 0
    for f in files:
        result = clean_file(f, dry_run=args.dry_run)
        if result["legacy_count"] > 0:
            status = "预览" if args.dry_run else ("✓" if not result["error"] else "✗")
            print("  [{}] {}: {} 个 legacy marker{}".format(
                status,
                f.name,
                result["legacy_count"],
                " → 已清理" if not args.dry_run and not result["error"] else ""
            ))
            if result["error"]:
                print("      ERROR: {}".format(result["error"]))
                total_errors += 1
        total_legacy += result["legacy_count"]
        total_cleaned += result["cleaned"] if not args.dry_run else 0

    print()
    print("-" * 60)
    print("总计: 扫描 {} 文件, 发现 {} legacy marker".format(len(files), total_legacy))
    if not args.dry_run:
        print("      清理 {} marker, 错误 {}".format(total_cleaned, total_errors))
    print()

    if total_legacy == 0:
        print("✓ 未发现 legacy marker，无需清理")
    elif args.dry_run:
        print("预览完成，加 --execute 参数实际清理")
    else:
        print("✓ 清理完成")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
