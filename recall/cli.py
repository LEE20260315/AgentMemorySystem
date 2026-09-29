"""CLI entry point: `memory ingest | recall | status | watch | logs`."""

from __future__ import annotations

import argparse
import os
import sys

from . import config, logs, store


def _conn(args):
    return store.connect(args.db)


def cmd_ingest(args) -> int:
    from . import runner
    summary = runner.run_sync()
    print(f"摄取完成：{summary['files']} 文件 → {summary['entries']} 条目"
          f"（{runner.format_summary(summary)}）")
    if summary["errors"]:
        print(f"  （{summary['errors']} 个文件读取失败）", file=sys.stderr)
    for agent, n in sorted(summary["by_agent"].items(), key=lambda kv: -kv[1]):
        print(f"  {agent:<12} {n}")
    return 0


def cmd_recall(args) -> int:
    conn = _conn(args)
    try:
        results = store.search(conn, args.query, args.limit)
    finally:
        conn.close()
    # G-E4: the ONE allowed write side-effect (best-effort; never fatal).
    from . import telemetry
    telemetry.log_query(args.query, args.limit, results)
    if not results:
        print("（无匹配）")
        return 0
    for i, r in enumerate(results, 1):
        title = r["title"] or r["file"]
        n = r.get("n_sources", 1)
        head = f"{i}. [{r['agent']}] {title}"
        if n > 1:
            head += f"  （合并 {n} 源）"
        print(head)
        print(f"      {r['file']}")
        if n > 1:
            agents = ", ".join(s["agent"] for s in r["sources"])
            print(f"      来源: {agents}")
        matched = r.get("matched_terms") or []
        hit = f"   （命中: {', '.join(matched)}）" if matched else ""
        print(f"      {r['snippet']}{hit}")
        print()
    return 0


def cmd_status(args) -> int:
    conn = _conn(args)
    try:
        s = store.stats(conn)
    finally:
        conn.close()
    print(f"索引：{args.db}")
    print(f"条目总数：{s['total']}")
    for agent, n in s["by_agent"]:
        print(f"  {agent:<12} {n}")
    return 0


def cmd_logs(args) -> int:
    path = logs.today_log_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        print(f"（暂无日志：{path}）")
        return 0
    tail = lines[-args.tail:]
    print(f"# {path}")
    print("".join(tail), end="")
    return 0


def cmd_stats(args) -> int:
    """T-E4: summarise the query-telemetry window (G-E4 evidence)."""
    from . import telemetry
    s = telemetry.stats(args.days)
    print(f"# {telemetry.query_log_dir()}")
    for line in telemetry.render_stats(s):
        print(line)
    return 0


def cmd_autopilot(args) -> int:
    """Run or control the local, whitelist-only autonomous loop."""
    from . import autopilot
    if args.stop:
        autopilot.set_stopped(True)
        print(f"自动循环已停止：{autopilot.stop_path()}")
        return 0
    if args.resume:
        autopilot.set_stopped(False)
        print("自动循环已恢复")
        return 0
    if args.status:
        import json
        print(json.dumps(autopilot.status(), ensure_ascii=False, indent=2))
        return 0
    import json
    result = autopilot.run_once(max_tasks=args.max_tasks)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("locked") or result.get("stopped"):
        return 0
    return 2 if result.get("failed") or result.get("rejected") else 0


def cmd_config(args) -> int:
    """Show the config actually in effect on THIS device (cross-device triage)."""
    cfg_path = config._config_file_path()
    has_cfg = bool(config.load_portable_config())
    src = os.path.dirname(cfg_path)
    print(f"程序目录：{src}")
    print(f"配置文件：{cfg_path}")
    print(f"          {'✓ 已加载（覆盖内置默认）' if has_cfg else '✗ 未找到，使用内置默认'}")
    print(f"索引库　：{logs.index_db_path()}")
    print(f"日志目录：{logs.logs_dir()}")
    print(f"同步间隔：{config.sync_interval_minutes()} 分钟")
    print("记忆来源（只读）：")
    for agent, root, _glob in config.sources():
        p = config._expand(root)
        mark = "✓ 存在" if os.path.isdir(p) else "✗ 不存在"
        print(f"  {agent:<8} {root:<24} {mark}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memory", description="召回优先的 Agent 记忆检索")
    p.add_argument("--db", default=logs.index_db_path(),
                   help="索引库路径（默认 %%LOCALAPPDATA%%\\recall-memory\\index.db）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ingest", help="只读扫描各 Agent 记忆并建立索引（跑一次）")

    r = sub.add_parser("recall", help="按关键词召回 top-K 记忆")
    r.add_argument("query", help="查询关键词")
    r.add_argument("--limit", type=int, default=5)

    sub.add_parser("status", help="查看索引概况")

    w = sub.add_parser("watch", help="定时同步守护进程（观察期用）")
    w.add_argument("--once", action="store_true", help="只跑一次同步后退出")
    w.add_argument("--interval-minutes", type=float,
                   default=config.sync_interval_minutes(),
                   help="同步间隔（分钟），默认取配置")

    lg = sub.add_parser("logs", help="查看今日日志尾部")
    lg.add_argument("--tail", type=int, default=30)

    st = sub.add_parser("stats", help="查询遥测统计（G-E4：每日查询数 / 零查询天数 / 判定）")
    st.add_argument("--days", type=int, default=14, help="统计最近多少天（默认 14）")

    sub.add_parser("config", help="显示本机实际生效的配置与来源可用性")

    ap = sub.add_parser("autopilot", help="运行或控制受控自主循环")
    ap.add_argument("--max-tasks", type=int, default=4,
                    help="每轮最多执行多少个白名单任务（默认 4）")
    ap.add_argument("--status", action="store_true", help="查看队列和运行状态")
    ap.add_argument("--stop", action="store_true", help="创建停止开关")
    ap.add_argument("--resume", action="store_true", help="移除停止开关")
    return p


def _force_utf8() -> None:
    """Force UTF-8 on stdout/stderr.

    A frozen exe whose output is piped/redirected falls back to the system
    locale encoding (cp936 on Chinese Windows), which turns Chinese output into
    mojibake. Forcing UTF-8 keeps console, pipe and tool capture all consistent.
    Best-effort: never fatal.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def _boot_marker() -> None:
    """Best-effort boot marker for headless diagnosis (scheduled task has no
    console/stderr). Writes one line to the recall-memory root; never raises."""
    import datetime as _dt
    line = f"{_dt.datetime.now().isoformat(timespec='seconds')} boot argv={sys.argv[1:]} frozen={getattr(sys, 'frozen', False)}\n"
    for base in (os.environ.get("LOCALAPPDATA"), os.path.expanduser("~")):
        if not base:
            continue
        try:
            d = os.path.join(base, "recall-memory")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "boot.log"), "a", encoding="utf-8") as f:
                f.write(line)
            return
        except OSError:
            continue


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    _boot_marker()
    args = build_parser().parse_args(argv)
    if args.cmd == "ingest":
        return cmd_ingest(args)
    if args.cmd == "recall":
        return cmd_recall(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "watch":
        from . import watch
        return watch.main(args)
    if args.cmd == "logs":
        return cmd_logs(args)
    if args.cmd == "stats":
        return cmd_stats(args)
    if args.cmd == "config":
        return cmd_config(args)
    if args.cmd == "autopilot":
        return cmd_autopilot(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())