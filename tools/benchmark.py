#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""性能基准：写入 / 查询 / 融合三档，产出可复现的对比数据。

背景
----
TODO P2-11。此前本机 shared.db 只有百来条记忆，任何"慢"都只停留在
猜测层面——v2.4.0 修的 replace churn、v2.5.4 的批量取标签，都没有量化
依据。本工具把规模拉到十万级，让优化前后有同一把尺子。

测什么
------
1. **写入**：`insert_memories_batch` 分批灌入（同时写 memories + FTS + tags）
2. **查询**：`get_memory`（主键）/ `list_memories`（分页，含标签批量取）/
   `search_by_keyword`（LIKE，命中率≈0.1%）
3. **融合**：`MemoryMerger.full_sync` 首轮（空共享库）与稳态（无变化再跑一轮）

不测什么：网络与 OneDrive 同步（那是 IO 延迟不是代码路径）；
向量搜索（依赖 sentence-transformers，见 ``--skip-vector`` 说明）。

用法
----
    # 默认 10 万条（较慢，分钟级）
    python tools/benchmark.py

    # 快速冒烟（1000 条，几秒）——改动后先跑这个
    python tools/benchmark.py --quick

    # 自定义规模并留存结果
    python tools/benchmark.py --n 50000 --json docs/benchmark/v2.5.5.json

    # 与历史基线对比（优化前后）
    python tools/benchmark.py --quick --compare docs/benchmark/baseline.json

通用性
------
不含任何硬编码用户路径：临时库建在系统临时目录，跑完默认删除
（``--keep-db`` 可保留）。不读不写真实数据根，因此可以随时随便跑。
纯标准库 + 项目代码，跨平台。

输出
----
- 终端：对齐表格 + 每条场景的吞吐
- `--json PATH`：结构化结果（含环境信息：Python 版本 / 平台 / 时间戳），
  供 `--compare` 做前后对比
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_memory as am  # noqa: E402

BATCH = 1000
# LIKE 查询关键词：内容里按此比例埋入，使命中率可控（不是全表扫描也不是空结果）
KEYWORD = "基准关键词"
KEYWORD_EVERY = 1000

DOMAINS = ("general", "deploy", "research", "ops", "personal")
TAGS = ("部署", "共享", "运维", "调研", "待办", "已归档")


# ---------------------------------------------------------------------------
# 数据生成
# ---------------------------------------------------------------------------
def make_entry(i: int, agent_id: str = "bench", base_time: datetime = None):
    """造一条记忆。内容里每 KEYWORD_EVERY 条埋一次关键词，控制搜索命中率。"""
    base_time = base_time or datetime(2026, 1, 1, tzinfo=timezone.utc)
    ts = (base_time + timedelta(seconds=i)).isoformat()
    if i % KEYWORD_EVERY == 0:
        content = "第 {} 条：{} 命中内容，用于测量 LIKE 搜索的返回成本。".format(i, KEYWORD)
    else:
        content = "第 {} 条常规记忆内容，长度适中，用于测量写入与分页开销。".format(i)
    return am.MemoryEntry(
        id="bench_{:07d}".format(i),
        agent_id=agent_id,
        timestamp=ts,
        source_device="bench-device",
        domain=DOMAINS[i % len(DOMAINS)],
        tags=[TAGS[i % len(TAGS)], TAGS[(i * 7) % len(TAGS)]],
        confidence=("high", "medium", "low")[i % 3],
        conflict_with=None,
        content=content,
        embedding=None,
        access_count=i % 50,
        last_accessed=ts,
        source_memory_id=None,
    )


# ---------------------------------------------------------------------------
# 计时
# ---------------------------------------------------------------------------
class Timer:
    """上下文计时器；多次 enter 累加，便于测稳态多轮。"""

    def __init__(self):
        self.total = 0.0
        self.count = 0

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.total += time.perf_counter() - self._t0
        self.count += 1
        return False


def rate(n: int, seconds: float):
    """每秒条数；耗时为 0 时返回 inf 会污染表格，返回 None。"""
    if seconds <= 0:
        return None
    return round(n / seconds, 1)


# ---------------------------------------------------------------------------
# 各阶段基准
# ---------------------------------------------------------------------------
def bench_write(db_path: Path, n: int) -> dict:
    """写入基准：分 BATCH 批灌入 n 条（含 FTS + tags 落库）。"""
    t = Timer()
    with am.MemoryDatabase(db_path) as db:
        written = 0
        for start in range(0, n, BATCH):
            chunk = [make_entry(i) for i in range(start, min(start + BATCH, n))]
            with t:
                written += db.insert_memories_batch(chunk)
        db.conn.commit()
        rows = db.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        fts = 0
        try:
            fts = db.conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        except sqlite3.OperationalError:
            pass

    size_mb = round(db_path.stat().st_size / 1024 / 1024, 2) if db_path.exists() else 0
    return {
        "scenario": "写入（分批 {} 条/批）".format(BATCH),
        "items": written,
        "seconds": round(t.total, 3),
        "per_sec": rate(written, t.total),
        "note": "库 {} 条 / FTS {} 行 / 文件 {} MB".format(rows, fts, size_mb),
        "db_rows": rows,
        "fts_rows": fts,
        "db_size_mb": size_mb,
    }


def bench_query(db_path: Path, n: int, samples: int) -> list:
    """查询基准：主键 / 分页 / 关键词搜索。"""
    out = []
    with am.MemoryDatabase(db_path) as db:
        # 1) 主键查询
        ids = ["bench_{:07d}".format(random.randrange(n)) for _ in range(samples)]
        t = Timer()
        with t:
            for mid in ids:
                db.get_memory(mid, track_access=False)
        out.append({
            "scenario": "get_memory（主键，{} 次）".format(samples),
            "items": samples, "seconds": round(t.total, 3),
            "per_sec": rate(samples, t.total), "note": "含标签查询",
        })

        # 2) 分页（尾部页最坏情况：OFFSET 大）
        t = Timer()
        with t:
            db.list_memories(limit=50, offset=max(0, n - 50))
        out.append({
            "scenario": "list_memories（尾页 50 条）",
            "items": 50, "seconds": round(t.total, 3),
            "per_sec": rate(50, t.total), "note": "OFFSET {} 深翻页".format(max(0, n - 50)),
        })

        # 3) 关键词搜索
        t = Timer()
        with t:
            hits = db.search_by_keyword(KEYWORD, limit=20)
        out.append({
            "scenario": "search_by_keyword（LIKE）",
            "items": len(hits), "seconds": round(t.total, 3),
            "per_sec": rate(len(hits), t.total),
            "note": "埋点命中约 {} 条".format(n // KEYWORD_EVERY),
        })

        # 4) 标签过滤（走 memory_tags JOIN）
        t = Timer()
        with t:
            tagged = db.list_memories(tags=[TAGS[0]], limit=50)
        out.append({
            "scenario": "list_memories（按标签过滤）",
            "items": len(tagged), "seconds": round(t.total, 3),
            "per_sec": rate(len(tagged), t.total), "note": "memory_tags JOIN",
        })
    return out


def bench_merge(workdir: Path, merge_n: int) -> list:
    """融合基准：首轮（空共享库）与稳态（无变化再跑一轮）。"""
    out = []
    agent_db = workdir / "agent_merge.db"
    shared_db = workdir / "shared_merge.db"

    with am.MemoryDatabase(agent_db) as db:
        for start in range(0, merge_n, BATCH):
            db.insert_memories_batch(
                [make_entry(i, agent_id="alpha")
                 for i in range(start, min(start + BATCH, merge_n))])

    # 首轮：共享库为空
    merger = am.create_merger(
        shared_db,
        agent_configs={"alpha": agent_db},
        conflict_strategy="newer_wins",
    )
    t = Timer()
    with t:
        first = merger.full_sync()
    inserted = first.get("alpha_to_shared", {}).get("inserted", 0)
    out.append({
        "scenario": "融合 首轮（空共享库）",
        "items": inserted, "seconds": round(t.total, 3),
        "per_sec": rate(inserted, t.total),
        "note": "{} 条入库".format(inserted),
    })

    # 稳态：无变化再跑一轮（日常同步的真实成本）
    t = Timer()
    with t:
        second = merger.full_sync()
    changed = sum(
        (v or {}).get("inserted", 0) + (v or {}).get("replaced", 0)
        for v in second.values() if isinstance(v, dict)
    )
    out.append({
        "scenario": "融合 稳态（无变化）",
        "items": merge_n, "seconds": round(t.total, 3),
        "per_sec": rate(merge_n, t.total),
        "note": "本轮变动 {} 条（应为 0）".format(changed),
    })
    return out


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def environment() -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "app_version": getattr(am, "__version__", None) or _app_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _app_version():
    try:
        from memory_sync_app import __version__
        return __version__
    except Exception:
        return None


def print_table(results: list):
    print("")
    header = "{:<26}{:>10}{:>12}{:>14}  {}".format("场景", "条数", "耗时(s)", "吞吐(条/s)", "备注")
    print(header)
    print("-" * max(len(header), 80))
    for r in results:
        print("{:<26}{:>10}{:>12}{:>14}  {}".format(
            r["scenario"][:26], r["items"], r["seconds"],
            r["per_sec"] if r["per_sec"] is not None else "-",
            r.get("note", "")))
    print("")


def compare(curr: list, base: dict):
    """与历史基线对比：按场景名配对，给出耗时涨跌。"""
    old = {r["scenario"]: r for r in base.get("results", [])}
    if not old:
        print("基线文件里没有可对比的场景。")
        return
    print("与基线对比（{}）：".format(base.get("env", {}).get("timestamp", "?")))
    print("{:<26}{:>12}{:>12}{:>12}".format("场景", "基线(s)", "本次(s)", "变化"))
    print("-" * 66)
    for r in curr:
        o = old.get(r["scenario"])
        if not o or not o.get("seconds"):
            continue
        delta = r["seconds"] - o["seconds"]
        pct = (delta / o["seconds"] * 100) if o["seconds"] else 0
        mark = "+" if delta > 0 else ""
        print("{:<26}{:>12}{:>12}{:>11.1f}%".format(
            r["scenario"][:26], o["seconds"], r["seconds"], pct))
        _ = mark
    print("")


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="AgentMemorySystem 性能基准（写入 / 查询 / 融合）")
    ap.add_argument("--n", type=int, default=100000,
                    help="写入与查询基准的条数（默认 100000）")
    ap.add_argument("--merge-n", type=int, default=5000,
                    help="融合基准条数（默认 5000，融合比写入重得多）")
    ap.add_argument("--samples", type=int, default=200,
                    help="主键查询采样次数（默认 200）")
    ap.add_argument("--quick", action="store_true",
                    help="冒烟档：n=1000, merge-n=200, samples=50")
    ap.add_argument("--json", dest="json_path",
                    help="把结构化结果写到该路径（供 --compare 复用）")
    ap.add_argument("--compare", dest="compare_path",
                    help="与历史 JSON 基线对比")
    ap.add_argument("--keep-db", action="store_true",
                    help="保留临时数据库目录（默认跑完删除）")
    ap.add_argument("--seed", type=int, default=20260907, help="随机种子（默认固定，保证可复现）")
    args = ap.parse_args(argv)

    if args.quick:
        args.n, args.merge_n, args.samples = 1000, 200, 50

    random.seed(args.seed)
    # random 只在采样处使用；生成内容是确定性的
    print("=" * 70)
    print("AgentMemorySystem 性能基准  v{}".format(_app_version() or "?"))
    print("规模: 写入/查询 {} 条 · 融合 {} 条 · 主键采样 {} 次".format(
        args.n, args.merge_n, args.samples))
    print("=" * 70)

    workdir = Path(tempfile.mkdtemp(prefix="ams_bench_"))
    results = []
    try:
        db_path = workdir / "bench.db"
        started = time.perf_counter()
        results.append(bench_write(db_path, args.n))
        results.extend(bench_query(db_path, args.n, args.samples))
        results.extend(bench_merge(workdir, args.merge_n))
        wall = time.perf_counter() - started
    finally:
        if args.keep_db:
            print("临时库保留在: {}".format(workdir))
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    print_table(results)
    print("总耗时 {:.1f}s".format(wall))

    if args.compare_path:
        base_path = Path(args.compare_path)
        if base_path.exists():
            with open(base_path, "r", encoding="utf-8") as f:
                compare(results, json.load(f))
        else:
            print("基线文件不存在: {}".format(base_path))

    if args.json_path:
        out_path = Path(args.json_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "env": environment(),
            "config": {
                "n": args.n, "merge_n": args.merge_n,
                "samples": args.samples, "seed": args.seed,
                "batch": BATCH,
            },
            "results": results,
        }
        # 与仓库其它 md/py 一致：写 LF，避免 CRLF 噪音 diff
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("结果已写入: {}".format(out_path))

    return 0


if __name__ == "__main__":
    sys.exit(main())
