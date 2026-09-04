#!/usr/bin/env python3
"""统一数据根目录（v2.1.0 一次性迁移工具）

背景：
    历史版本中 SyncEngine 硬编码使用 <repo>/data 作为融合层根目录，
    而 GUI / SyncState / BAT 启动器使用 <repo>/AgentMemory（AGENT_MEMORY_DATA_DIR）。
    两处分裂导致：
      - 同步引擎在 data/ 下读写 agent_* / shared.db
      - GUI 心跳/设置/状态写在 AgentMemory/
      - 用户看到的同步效果与数据位置不一致

本脚本把 data/ 下的真实数据（agent_*、shared.db、device_config.json 等）
复制合并到 AgentMemory/（统一数据根），保留 data/ 作为备份不删除。

用法：
    python tools/unify_data_root.py [--merge-settings] [--remove-data]

参数：
    --merge-settings  合并 sync_settings.json（激活 auto_sync/auto_start/1h）
    --remove-data     迁移完成后删除 data/（默认保留备份）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
TARGET_DIR = REPO / "AgentMemory"


def merge_settings(force: bool = False):
    """合并两个 sync_settings.json：以 AgentMemory 为准，补上 data 的激活字段。"""
    target = TARGET_DIR / "sync_settings.json"
    data = DATA_DIR / "sync_settings.json"
    if not target.exists():
        if data.exists():
            shutil.copy2(data, target)
            print("[merge] 复制 data/sync_settings.json -> AgentMemory/")
        return
    if not data.exists():
        return
    try:
        t = json.loads(target.read_text(encoding="utf-8"))
        d = json.loads(data.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    changed = False
    # data/ 中更"激活"的字段优先（auto_start/auto_sync/更短间隔）
    for key in ("auto_start", "auto_sync"):
        if d.get(key) and not t.get(key):
            t[key] = d[key]
            changed = True
            print(f"[merge] auto 字段: {key} -> {d[key]}")
    if d.get("auto_interval_hours") and t.get("auto_interval_hours") is None:
        t["auto_interval_hours"] = d["auto_interval_hours"]
        changed = True
        print(f"[merge] auto_interval_hours -> {d['auto_interval_hours']}")
    elif d.get("auto_interval_hours") and t.get("auto_interval_hours", 99) > d["auto_interval_hours"]:
        # 取更短间隔（更活跃），避免 4h 长间隔导致几乎不同步
        old = t["auto_interval_hours"]
        t["auto_interval_hours"] = d["auto_interval_hours"]
        changed = True
        print(f"[merge] auto_interval_hours: {old}h -> {d['auto_interval_hours']}h")
    if changed:
        target.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[merge] AgentMemory/sync_settings.json 已更新")


def main():
    ap = argparse.ArgumentParser(description="统一数据根目录")
    ap.add_argument("--merge-settings", action="store_true")
    ap.add_argument("--remove-data", action="store_true")
    args = ap.parse_args()

    if not DATA_DIR.exists():
        print("[skip] data/ 不存在，无需迁移")
        # 仍然合并设置（例如仅有 AgentMemory/ 的全新安装）
        if args.merge_settings:
            merge_settings()
        return 0

    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    moved = 0
    for item in sorted(DATA_DIR.iterdir()):
        target = TARGET_DIR / item.name
        if target.exists():
            # 不覆盖已有文件（保留目标目录优先），但记录冲突
            print(f"[skip] {item.name} 已存在于目标目录")
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
            moved += 1
            print(f"[copy] {item.name}")
        except Exception as e:
            print(f"[warn] 复制 {item.name} 失败: {e}")

    if args.merge_settings:
        merge_settings()

    print(f"\n完成：从 data/ 迁移 {moved} 项到 AgentMemory/")
    print("目标数据根（统一）: {}".format(TARGET_DIR))

    if args.remove_data:
        shutil.rmtree(DATA_DIR)
        print("[done] data/ 已删除（迁移完成）")
    else:
        print("[keep] data/ 保留作为备份，确认无误后可删除")

    return 0


if __name__ == "__main__":
    sys.exit(main())
