#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日志保留扫描/清理工具：盘点本机与数据根的诊断日志，按 (数量, 天数) 裁剪。

背景
----
v2.3.0 起 ``memory_sync_app`` 在每次轮转时会按 (keep_count, keep_days)
自动裁剪**它自己正在写**的日志。但两类文件代码不会碰：

1. **停止写入的历史文件**——例如 v2.2.1 把日志从数据根 `.logs/` 迁到
   `LOCALAPPDATA` 之后，数据根里那批 `agent_memory*.log` 就成了死文件，
   却一直跟着 OneDrive 同步；
2. **其它历史命名的遗留文件**——早期版本按天/按进程名生成的
   `tray_error-*.log` 之类，实测堆积到 86 个 / 36MB。

这些需要有人**看一眼再决定**。本工具默认只出报告，加 `--apply` 才删除，
且删除走回收站（可恢复）。

用法
----
    # 预览报告（默认，不删除任何东西）
    python tools/log_retention.py

    # 执行清理（送回收站）
    python tools/log_retention.py --apply

    # 自定义保留策略
    python tools/log_retention.py --keep-days 14 --keep-count 5

    # 指定数据根（不自动探测）
    python tools/log_retention.py --root /path/to/AgentMemory

通用性
------
不含任何硬编码用户路径：数据根通过 ``safe_io.get_data_root()`` 探测，
本机目录通过 ``LOCALAPPDATA`` 环境变量；两者都不可用则该处跳过。
POSIX 下没有回收站 API，删除退化为 ``os.remove``（此时务必先跑预览）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

DEFAULT_KEEP_DAYS = 7
DEFAULT_KEEP_COUNT = 3
# mtime 在 1 天内的文件视为「活跃」，任何策略下都不动——避免误删正在写的文件
ACTIVE_WINDOW_SECONDS = 86400


# ---------------------------------------------------------------------------
# 定位
# ---------------------------------------------------------------------------
def local_log_dirs():
    """本机日志目录（LOCALAPPDATA，绝不在 OneDrive 里）。"""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return []
    root = Path(base) / "AgentMemorySystem"
    return [d for d in (root, root / "logs") if d.is_dir()]


def data_log_dirs(root=None):
    """数据根日志目录（OneDrive 同步目录，只读诊断副本）。"""
    if root is None:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from safe_io import get_data_root
            root = get_data_root()
        except Exception:
            return []
    root = Path(root)
    return [d for d in (root, root / ".logs") if d.is_dir()]


def project_log_dir():
    """项目根（仓库目录）里的日志。"""
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 扫描与决策
# ---------------------------------------------------------------------------
def scan_log_files(root=None):
    """扫描所有已知日志位置，返回记录列表。

    每条记录为 dict：``path / size / mtime / age_days / zone``，
    其中 zone 为 ``local``（本机）或 ``data``（数据根/OneDrive）。
    """
    records = []
    seen = set()

    def _collect(directory, zone):
        try:
            entries = sorted(directory.glob("*.log"))
        except OSError:
            return
        for p in entries:
            try:
                if not p.is_file() or p in seen:
                    continue
                st = p.stat()
            except OSError:
                continue
            seen.add(p)
            records.append({
                "path": p,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "age_days": (time.time() - st.st_mtime) / 86400.0,
                "zone": zone,
            })

    for d in local_log_dirs():
        _collect(d, "local")
    for d in data_log_dirs(root):
        _collect(d, "data")
    _collect(project_log_dir(), "data")

    records.sort(key=lambda r: (-r["size"], str(r["path"])))
    return records


def plan_prune(records, keep_days=DEFAULT_KEEP_DAYS, keep_count=DEFAULT_KEEP_COUNT):
    """把记录分成 (保留, 删除) 两组。

    决策顺序：
    1. mtime 在 1 天内 → **始终保留**（正在被写的文件绝不碰）；
    2. 年龄超过 keep_days → 删除；
    3. 同一 stem 的轮转文件超过 keep_count → 最旧的那些删除。

    Returns
    -------
    (keep, drop) : tuple[list[dict], list[dict]]
    """
    now = time.time()
    keep, drop = [], []

    # 按 (目录, stem) 分组统计轮转文件。轮转文件命名形如
    # ``tray_error.20260829-203700.log``，stem 取第一个 "." 之前的部分。
    groups: dict = {}
    for r in records:
        name = r["path"].name
        stem = name.split(".", 1)[0]
        groups.setdefault((r["path"].parent, stem), []).append(r)

    excess = set()
    for items in groups.values():
        if len(items) <= keep_count:
            continue
        # 主文件 <stem>.log 不参与计数：它永远是活跃文件
        rotated = [r for r in items if r["path"].name != stem_of(r["path"])]
        rotated.sort(key=lambda r: r["mtime"])
        n_drop = len(items) - keep_count
        for r in rotated[:max(0, n_drop)]:
            excess.add(r["path"])

    for r in records:
        if now - r["mtime"] < ACTIVE_WINDOW_SECONDS:
            keep.append(r)
        elif keep_days > 0 and r["age_days"] > keep_days:
            drop.append(r)
        elif r["path"] in excess:
            drop.append(r)
        else:
            keep.append(r)
    return keep, drop


def stem_of(path: Path) -> str:
    """``tray_error.20260829.log`` → ``tray_error.log`` 的主文件名。"""
    name = path.name
    return name.split(".", 1)[0] + ".log"


# ---------------------------------------------------------------------------
# 删除（Windows 走回收站，POSIX 退化为 os.remove）
# ---------------------------------------------------------------------------
def to_recycle_bin(path: Path) -> bool:
    """删除单个文件；成功（文件已不存在）返回 True。"""
    p = str(path)
    if not os.path.exists(p):
        return True
    if os.name != "nt":
        try:
            os.remove(p)
            return True
        except OSError:
            return False

    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    try:
        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = 3  # FO_DELETE
        # 必须显式持有缓冲区：c_wchar_p 字段只存指针，赋临时字符串会被 GC
        # 回收成悬垂指针，表现为 rc=2 / rc=120 之类的随机失败。
        from_buf = ctypes.create_unicode_buffer(os.path.abspath(p) + "\0\0")
        op.pFrom = ctypes.cast(from_buf, wintypes.LPCWSTR)
        op.pTo = None
        op.fFlags = 0x0040 | 0x0010 | 0x0400 | 0x0004 | 0x0200
        ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        del from_buf  # 保持引用到调用结束
    except Exception:
        return False
    # 返回码不可信（部分成功也会报非 0），以文件是否消失为准
    return not os.path.exists(p)


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "{:.1f}{}".format(n, unit) if unit != "B" else "{:.0f}B".format(n)
        n /= 1024.0
    return "{:.1f}GB".format(n)


def print_report(keep, drop, apply_changes=False):
    total = sum(r["size"] for r in keep + drop)
    print("=" * 72)
    print("日志保留扫描报告" + ("（执行模式）" if apply_changes else "（预览模式，未删除任何文件）"))
    print("=" * 72)
    print("保留 {:>3} 个 / {:<10}   删除 {:>3} 个 / {:<10}   总计 {}".format(
        len(keep), _human(sum(r["size"] for r in keep)),
        len(drop), _human(sum(r["size"] for r in drop)),
        _human(total)))
    print()

    for title, items in (("保留", keep), ("删除", drop)):
        if not items:
            continue
        print("--- {}（{} 个）---".format(title, len(items)))
        for r in sorted(items, key=lambda r: r["mtime"]):
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"]))
            print("  {:>10}  {:>7.1f}d  [{}]  {}".format(
                _human(r["size"]), r["age_days"], r["zone"], r["path"]))
        print()

    # 数据根（OneDrive）里的死文件是最值得关注的：它们会持续被同步
    stale_data = [r for r in drop if r["zone"] == "data"]
    if stale_data:
        print("注意：其中 {} 个位于数据根（OneDrive 同步目录），共 {} ——"
              " 清理它们可以同时减少同步流量。".format(
                  len(stale_data), _human(sum(r["size"] for r in stale_data))))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="日志保留扫描/清理（默认预览，不删除）")
    ap.add_argument("--apply", action="store_true", help="真正执行删除（送回收站）")
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS,
                    help="保留最近 N 天的日志（默认 %d）" % DEFAULT_KEEP_DAYS)
    ap.add_argument("--keep-count", type=int, default=DEFAULT_KEEP_COUNT,
                    help="同一日志（主文件+轮转）最多保留 N 个（默认 %d）" % DEFAULT_KEEP_COUNT)
    ap.add_argument("--root", default=None, help="手动指定数据根，跳过自动探测")
    args = ap.parse_args(argv)

    records = scan_log_files(args.root)
    if not records:
        print("未发现任何日志文件。")
        return 0
    keep, drop = plan_prune(records, args.keep_days, args.keep_count)

    if args.apply:
        removed, failed = 0, []
        for r in drop:
            if to_recycle_bin(r["path"]):
                removed += 1
            else:
                failed.append(r)
        print_report(keep, drop, apply_changes=True)
        print("已删除 {} 个；失败 {} 个。".format(removed, len(failed)))
        for r in failed:
            print("  FAILED  {}".format(r["path"]))
        if os.name == "nt":
            print("（Windows 回收站中的文件仍占用磁盘空间，需手动清空回收站才会释放）")
        return 0 if not failed else 1

    print_report(keep, drop, apply_changes=False)
    print("这是预览。确认无误后加 --apply 执行（删除走回收站，可恢复）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
