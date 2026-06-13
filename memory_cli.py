#!/usr/bin/env python3
"""
Agent 记忆系统 - 命令行工具
用法:
  python memory_cli.py --agent claude write "这是一条记忆" --tags 测试 Python
  python memory_cli.py --agent claude search "Python"
  python memory_cli.py --agent claude sync
  python memory_cli.py --agent claude merge --with hermes
"""

import argparse
import sys
from pathlib import Path
import json
from datetime import datetime, timezone

import agent_memory as am


def find_agent_dir(root: Path, agent_id: str) -> Path:
    """查找 Agent 目录 - 兼容 agent_<id> 和 <id> 两种命名"""
    # v1.2 约定: agent_<id>
    candidates = [root / ("agent_" + agent_id), root / agent_id]
    for agent_dir in candidates:
        if agent_dir.exists():
            return agent_dir
    # 都不存在时报错
    print("错误: Agent 目录不存在: {} 或 {}".format(candidates[0], candidates[1]))
    print("请先运行: python setup_agent.py --agent {} --device <设备名> --root {}".format(agent_id, root))
    sys.exit(1)


def cmd_write(args):
    """写入记忆"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    # 智能兜底: device_config.json 缺失时自动创建
    device_config_path = agent_dir / "device_config.json"
    if not device_config_path.exists():
        # 优先检查本地代码目录 (v1.2 设计)
        local_code_dir = Path(__file__).parent
        local_dc = local_code_dir / "device_config.json"
        if local_dc.exists():
            device_config_path = local_dc
        else:
            # 兜底: 在 agent_dir 创建默认
            import socket
            default_device = socket.gethostname().lower().replace("\\", "_").replace(" ", "_")
            default_device = "agent_" + default_device if not default_device.startswith("agent_") else default_device
            device_config_path.write_text(
                json.dumps({"source_device": default_device}, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            print("[AUTO-CREATE] 缺少 device_config.json, 已自动创建: {}".format(device_config_path))
            print("             source_device = {}".format(default_device))

    # 启动
    am._context = None
    am.startup(agent_dir / "identity.json", device_config_path)

    # 写入
    tags = args.tags if args.tags else ["未分类"]
    confidence = args.confidence

    # Hotword Trigger 检测
    try:
        trigger = am.TriggerEngine(root=root)
        match = trigger.match_hotword(args.content)
        if match["matched"]:
            # 命中触发词: 强制 confidence 为 high, 追加 auto_tags
            old_conf = confidence
            confidence = match.get("force_confidence") or "high"
            for tag in match.get("auto_tags", []):
                if tag not in tags:
                    tags.append(tag)
            print("[HOTWORD] 命中触发词: {} (confidence: {} -> {})".format(
                ", ".join(match["matched_words"]), old_conf, confidence
            ))
    except Exception as e:
        # TriggerEngine 失败不影响写入
        pass

    mid = am.write_memory(
        content=args.content,
        tags=tags,
        confidence=confidence,
        domain=args.domain
    )

    # 同步到 SQLite
    am.sync_markdown_to_db()

    print("写入成功: {}".format(mid))


def cmd_search(args):
    """搜索记忆"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    # 启动
    am._context = None
    am.startup(agent_dir / "identity.json", agent_dir / "device_config.json")

    # 搜索
    results = am.search_memory(
        query=args.query,
        mode=args.mode,
        tags=args.tags,
        domain=args.domain,
        limit=args.limit,
        db_path=agent_dir / "memories.db"
    )

    if not results:
        print("未找到相关记忆")
        return

    print(f"找到 {len(results)} 条记忆:")
    for i, m in enumerate(results, 1):
        print(f"\n--- 记忆 {i} ---")
        print(f"ID: {m.id}")
        print(f"内容: {m.content}")
        print(f"标签: {', '.join(m.tags)}")
        print(f"置信度: {m.confidence}")
        print(f"时间: {m.timestamp}")


def cmd_list(args):
    """列出记忆"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    # 启动
    am._context = None
    am.startup(agent_dir / "identity.json", agent_dir / "device_config.json")

    # 列出
    memories = am.list_memories(
        domain=args.domain,
        tags=args.tags,
        limit=args.limit,
        db_path=agent_dir / "memories.db"
    )

    if not memories:
        print("暂无记忆")
        return

    print(f"共 {len(memories)} 条记忆:")
    for m in memories:
        tags_str = ", ".join(m.tags) if m.tags else "无"
        print(f"  [{m.id}] {m.content[:40]}... (标签: {tags_str})")


def cmd_sync(args):
    """同步记忆到 SQLite"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    # 启动
    am._context = None
    am.startup(agent_dir / "identity.json", agent_dir / "device_config.json")

    # 同步
    count = am.sync_markdown_to_db()
    print(f"同步完成: {count} 条记忆已索引")


def cmd_merge(args):
    """融合记忆"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)
    other_agent = args.with_agent
    other_dir = find_agent_dir(root, other_agent)

    # 创建融合器
    merger = am.create_merger(
        shared_db_path=root / "shared.db",
        agent_configs={
            args.agent: agent_dir / "memories.db",
            other_agent: other_dir / "memories.db"
        }
    )

    # 同步对方的记忆到共享库
    r1 = merger.sync_agent_to_shared(other_agent)
    print(f"同步 {other_agent} 到共享库: {r1}")

    # 从共享库同步到自己
    r2 = merger.sync_shared_to_agent(args.agent)
    print(f"从共享库同步到 {args.agent}: {r2}")

    print("融合完成! 现在可以搜索对方的记忆了")


def cmd_full_merge(args):
    """完整融合（所有Agent）"""
    root = Path(args.root)

    # 查找所有 Agent 目录
    agent_dirs = {}
    for d in root.iterdir():
        if d.is_dir() and d.name != "_shared" and (d / "identity.json").exists():
            agent_dirs[d.name] = d / "memories.db"

    if len(agent_dirs) < 2:
        print("错误: 至少需要2个Agent才能融合")
        print(f"当前Agent: {list(agent_dirs.keys())}")
        sys.exit(1)

    print(f"发现 {len(agent_dirs)} 个Agent: {list(agent_dirs.keys())}")

    # 创建融合器
    merger = am.create_merger(
        shared_db_path=root / "shared.db",
        agent_configs=agent_dirs
    )

    # 完整同步
    results = merger.full_sync()
    print("融合完成!")
    for key, value in results.items():
        print(f"  {key}: {value}")


def cmd_import_trae(args):
    """导入 Trae 记忆"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    # 启动
    am._context = None
    am.startup(agent_dir / "identity.json", agent_dir / "device_config.json")

    # 导入
    trae_dir = Path(args.trae_dir) if args.trae_dir else Path.home() / ".trae-cn" / "memory"
    count = am.import_trae_memories(
        trae_memory_dir=trae_dir,
        agent_id=args.agent,
        device_name=args.device,
        db_path=agent_dir / "memories.db"
    )

    print(f"导入完成: {count} 条 Trae 记忆已导入")


def cmd_safe_write(args):
    """安全写入（带并发控制）"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    # 启动
    am._context = None
    am.startup(agent_dir / "identity.json", agent_dir / "device_config.json")

    # 安全写入
    tags = args.tags if args.tags else ["未分类"]
    try:
        mid = am.safe_write_memory(
            content=args.content,
            tags=tags,
            confidence=args.confidence,
            domain=args.domain,
            max_retries=args.retries
        )
        print(f"写入成功: {mid}")
    except am.LockError as e:
        print(f"写入失败: {e}")
        print("提示: 可能有其他设备正在写入，请稍后重试")
        sys.exit(1)


def cmd_devices(args):
    """查看所有设备的记忆文件"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    # 获取所有设备记忆文件
    device_files = am.get_all_device_memories(agent_dir)

    if not device_files:
        print(f"未找到 {args.agent} 的记忆文件")
        return

    print(f"{args.agent} 的记忆文件:")
    for device_name, md_path in device_files.items():
        # 统计记忆数量
        text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        entries = am.parse_memories(text)
        print(f"  {device_name}: {md_path.name} ({len(entries)} 条记忆)")


def cmd_backup(args):
    """备份所有数据"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    results = am.backup_all(agent_dir)

    if results["backed_up"]:
        print(f"备份完成: {len(results['backed_up'])} 个文件")
        for f in results["backed_up"]:
            print(f"  {Path(f).name}")
    if results["errors"]:
        print(f"备份错误: {len(results['errors'])} 个")
        for e in results["errors"]:
            print(f"  {e}")


def cmd_check(args):
    """检查数据完整性"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    results = am.check_integrity(agent_dir)

    print("完整性检查结果:")
    print(f"  总文件数: {results['summary']['total_files']}")
    print(f"  总错误数: {results['summary']['total_errors']}")

    if results["markdown_files"]:
        print("\nMarkdown 文件:")
        for r in results["markdown_files"]:
            status = "✓" if not r["errors"] else "✗"
            print(f"  {status} {Path(r['file']).name}: {r['valid_entries']} 条有效, {r['invalid_entries']} 条无效")
            if r["errors"]:
                for e in r["errors"]:
                    print(f"    错误: {e}")

    if results["sqlite_databases"]:
        print("\nSQLite 数据库:")
        for r in results["sqlite_databases"]:
            status = "✓" if r["valid"] else "✗"
            print(f"  {status} {Path(r['file']).name}: {r['total_memories']} 条记忆")
            if r["errors"]:
                for e in r["errors"]:
                    print(f"    错误: {e}")


def cmd_optimize(args):
    """优化记忆"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    results = am.optimize_memories(memory_root=agent_dir)

    print("优化完成:")
    print(f"  压缩: 合并 {results['compress']['merged']} 条重复记忆")
    print(f"  索引: 重建 {results['rebuild']['rebuilt']} 条索引")


def cmd_report(args):
    """生成统计报告"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    report = am.get_memory_report(memory_root=agent_dir)

    print("记忆统计报告:")
    print(f"  总记忆数: {report['total_memories']}")

    if report["by_agent"]:
        print("\n按 Agent:")
        for agent, count in report["by_agent"].items():
            print(f"  {agent}: {count} 条")

    if report["by_domain"]:
        print("\n按领域:")
        for domain, count in report["by_domain"].items():
            print(f"  {domain}: {count} 条")

    if report["by_device"]:
        print("\n按设备:")
        for device, count in report["by_device"].items():
            print(f"  {device}: {count} 条")

    if report["by_confidence"]:
        print("\n按置信度:")
        for conf, count in report["by_confidence"].items():
            print(f"  {conf}: {count} 条")

    if report["oldest_memory"]:
        print(f"\n最早记忆: {report['oldest_memory']}")
    if report["newest_memory"]:
        print(f"最新记忆: {report['newest_memory']}")
    print(f"平均内容长度: {report['average_content_length']:.0f} 字符")


def cmd_smart_compress(args):
    """智能压缩记忆"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    results = am.smart_compress(
        target_count=args.target,
        min_score=args.min_score,
        dry_run=args.dry_run,
        memory_root=agent_dir
    )

    mode = "（试运行）" if args.dry_run else ""
    print("智能压缩完成{}:".format(mode))
    print(f"  原始数量: {results['total']}")
    print(f"  删除重复: {results['duplicates_removed']}")
    print(f"  合并相似: {results['similar_merged']}")
    print(f"  删除低分: {results['low_score_removed']}")
    print(f"  最终数量: {results['final_count']}")


def cmd_archive(args):
    """归档冷数据"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    results = am.archive_cold_memories(
        min_score=args.min_score,
        memory_root=agent_dir
    )

    print("归档完成:")
    print(f"  冷数据总数: {results['total_cold']}")
    print(f"  已归档: {results['archived']}")
    print(f"  保留: {results['kept']}")


def cmd_scores(args):
    """查看记忆重要性分数"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    scored = am.get_importance_scores(memory_root=agent_dir)

    print(f"记忆重要性分数 (共 {len(scored)} 条):")
    print("-" * 80)

    for i, (memory, score) in enumerate(scored[:args.limit], 1):
        print(f"{i:3d}. [{score:.2f}] {memory.content[:50]}...")
        print(f"     标签: {', '.join(memory.tags)}")
        print(f"     置信度: {memory.confidence}, 访问: {memory.access_count}次")
        print()


def cmd_health(args):
    """健康检查"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    result = am.health_check(memory_root=agent_dir)

    print(f"系统状态: {result['status'].upper()}")
    print("-" * 40)

    print("\n检查项:")
    for key, value in result["checks"].items():
        print(f"  {key}: {value}")

    if result["warnings"]:
        print(f"\n警告 ({len(result['warnings'])}):")
        for w in result["warnings"]:
            print(f"  ⚠ {w}")

    if result["errors"]:
        print(f"\n错误 ({len(result['errors'])}):")
        for e in result["errors"]:
            print(f"  ✗ {e}")


def cmd_expire(args):
    """清理过期记忆"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    result = am.expire_old_memories(memory_root=agent_dir, dry_run=args.dry_run)

    mode = "试运行" if args.dry_run else "执行"
    print(f"过期记忆清理 ({mode}):")
    print(f"  检查总数: {result['total_checked']}")
    print(f"  过期记忆: {result['expired_found']}")
    print(f"  已归档: {result['archived']}")

    if result["errors"]:
        print(f"\n错误:")
        for e in result["errors"]:
            print(f"  {e}")


def cmd_migrate(args):
    """数据库迁移"""
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)
    db_path = agent_dir / "memories.db"

    result = am.migrate_database(db_path)

    print("数据库迁移:")
    print(f"  当前版本: v{result['current_version']}")
    print(f"  目标版本: v{result['target_version']}")

    if result["already_up_to_date"]:
        print("  状态: 已是最新版本")
    else:
        print(f"\n已执行迁移 ({len(result['migrations_applied'])}):")
        for m in result["migrations_applied"]:
            print(f"  ✓ {m}")


def main():
    parser = argparse.ArgumentParser(description="Agent 记忆系统命令行工具")
    parser.add_argument("--root", default=str(Path(__file__).parent / "data"), help="数据根目录")
    parser.add_argument("--agent", default="claude", help="Agent ID")

    subparsers = parser.add_subparsers(dest="command", help="命令")

    # write
    write_parser = subparsers.add_parser("write", help="写入记忆")
    write_parser.add_argument("content", help="记忆内容")
    write_parser.add_argument("--tags", nargs="+", help="标签列表")
    write_parser.add_argument("--confidence", default="high", choices=["high", "medium", "low"], help="置信度")
    write_parser.add_argument("--domain", help="领域")

    # safe-write
    safe_write_parser = subparsers.add_parser("safe-write", help="安全写入（带并发控制）")
    safe_write_parser.add_argument("content", help="记忆内容")
    safe_write_parser.add_argument("--tags", nargs="+", help="标签列表")
    safe_write_parser.add_argument("--confidence", default="high", choices=["high", "medium", "low"], help="置信度")
    safe_write_parser.add_argument("--domain", help="领域")
    safe_write_parser.add_argument("--retries", type=int, default=3, help="最大重试次数")

    # search
    search_parser = subparsers.add_parser("search", help="搜索记忆")
    search_parser.add_argument("query", help="搜索关键词")
    search_parser.add_argument("--mode", default="keyword", choices=["keyword", "vector", "hybrid"], help="搜索模式")
    search_parser.add_argument("--tags", nargs="+", help="标签过滤")
    search_parser.add_argument("--domain", help="领域过滤")
    search_parser.add_argument("--limit", type=int, default=10, help="返回数量")

    # list
    list_parser = subparsers.add_parser("list", help="列出记忆")
    list_parser.add_argument("--tags", nargs="+", help="标签过滤")
    list_parser.add_argument("--domain", help="领域过滤")
    list_parser.add_argument("--limit", type=int, default=20, help="返回数量")

    # sync
    subparsers.add_parser("sync", help="同步记忆到 SQLite")

    # merge
    merge_parser = subparsers.add_parser("merge", help="融合记忆")
    merge_parser.add_argument("--with", dest="with_agent", required=True, help="要融合的Agent ID")

    # full-merge
    subparsers.add_parser("full-merge", help="完整融合（所有Agent）")

    # import-trae
    import_trae_parser = subparsers.add_parser("import-trae", help="导入 Trae 记忆")
    import_trae_parser.add_argument("--trae-dir", help="Trae 记忆目录 (默认 ~/.trae-cn/memory)")
    import_trae_parser.add_argument("--device", default="unknown", help="设备名称")

    # devices
    subparsers.add_parser("devices", help="查看所有设备的记忆文件")

    # backup
    subparsers.add_parser("backup", help="备份所有数据")

    # check
    subparsers.add_parser("check", help="检查数据完整性")

    # optimize
    subparsers.add_parser("optimize", help="优化记忆（压缩/去重/重建索引）")

    # report
    subparsers.add_parser("report", help="生成统计报告")

    # smart-compress
    smart_compress_parser = subparsers.add_parser("smart-compress", help="智能压缩记忆")
    smart_compress_parser.add_argument("--target", type=int, help="目标记忆数量")
    smart_compress_parser.add_argument("--min-score", type=float, default=0.3, help="最低重要性分数 (0-1)")
    smart_compress_parser.add_argument("--dry-run", action="store_true", help="试运行（不实际执行）")

    # archive
    archive_parser = subparsers.add_parser("archive", help="归档冷数据")
    archive_parser.add_argument("--min-score", type=float, default=0.3, help="最低重要性分数")

    # scores
    scores_parser = subparsers.add_parser("scores", help="查看记忆重要性分数")
    scores_parser.add_argument("--limit", type=int, default=20, help="显示数量")

    # health
    subparsers.add_parser("health", help="系统健康检查")

    # expire
    expire_parser = subparsers.add_parser("expire", help="清理过期记忆")
    expire_parser.add_argument("--dry-run", action="store_true", help="试运行（不实际执行）")

    # migrate
    subparsers.add_parser("migrate", help="数据库迁移")

    # discover (v1.3: 自动发现本地安装目录中的 Agent)
    discover_parser = subparsers.add_parser(
        "discover", help="自动发现本机所有 Agent 的本地安装目录与记忆文件"
    )
    discover_parser.add_argument(
        "--scan-root", action="store_true",
        help="不仅按 LOCAL_PATTERNS, 额外扫描 C:/Users/<user> 下的 .<agent> 目录"
    )

    # dashboard (v1.3: 双层视图 - 本地 + 融合)
    subparsers.add_parser(
        "dashboard", help="Dashboard (双层视图: 本地安装 + OneDrive 融合)"
    )

    # extract (v1.3 新增: 从本地记忆文件提取到 OneDrive 融合层)
    extract_parser = subparsers.add_parser(
        "extract", help="从 Agent 本地记忆文件提取并写入 OneDrive 融合层"
    )
    extract_parser.add_argument(
        "--agent", required=True, help="目标 Agent ID (如 hermes/claude)"
    )
    extract_parser.add_argument(
        "--local-file", action="append", default=[],
        help="显式指定本地文件 (可多次指定, 覆盖 Registry 自动发现)"
    )
    extract_parser.add_argument(
        "--dry-run", action="store_true", help="试运行, 只解析不写入"
    )

    # flush
    flush_parser = subparsers.add_parser("flush", help="批量落盘 SessionFlusher 暂存区的条目")
    flush_parser.add_argument("--dry-run", action="store_true", help="试运行（不实际写入）")

    # full-sync (新增: 完整同步流程)
    subparsers.add_parser("full-sync", help="完整同步: 发现 → 提取 → 融合 → 写回各 Agent")

    # redetect (新增: 重新检测 Agent)
    subparsers.add_parser("redetect", help="重新检测本机 Agent 路径")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "write": cmd_write,
        "safe-write": cmd_safe_write,
        "search": cmd_search,
        "list": cmd_list,
        "sync": cmd_sync,
        "merge": cmd_merge,
        "full-merge": cmd_full_merge,
        "import-trae": cmd_import_trae,
        "devices": cmd_devices,
        "backup": cmd_backup,
        "check": cmd_check,
        "optimize": cmd_optimize,
        "report": cmd_report,
        "smart-compress": cmd_smart_compress,
        "archive": cmd_archive,
        "scores": cmd_scores,
        "health": cmd_health,
        "expire": cmd_expire,
        "migrate": cmd_migrate,
        "discover": cmd_discover,
        "dashboard": cmd_dashboard,
        "extract": cmd_extract,
        "flush": cmd_flush,
        "full-sync": cmd_full_sync,
        "redetect": cmd_redetect,
    }

    commands[args.command](args)


# ---------------------------------------------------------------------------
# v1.3 新增命令: discover / dashboard / extract
# ---------------------------------------------------------------------------

def cmd_discover(args):
    """
    v1.3: 自动发现本机所有 Agent 的本地安装目录

    扫描 AgentRegistry.LOCAL_PATTERNS 列表 (~\\.<agent>),
    将每个 Agent 的安装路径 + 本地记忆文件登记到 Registry。
    """
    root = Path(args.root)
    registry = am.AgentRegistry(root=root)

    if getattr(args, "scan_root", False):
        # 额外扫描 %USERPROFILE% 下的 .<name> 目录
        user_home = Path.home()
        for child in user_home.iterdir():
            if not child.is_dir():
                continue
            name = child.name
            if not name.startswith("."):
                continue
            agent_id = name.lstrip(".")
            if any(agent_id == p[0] for p in registry.LOCAL_PATTERNS):
                continue
            # 自动检测常见记忆文件
            mems = []
            for c in ["MEMORY.md", "memory.md", "memories.md", "preferences.md",
                      "history.jsonl", "USER.md", "user.md"]:
                p = child / c
                if p.exists():
                    mems.append(str(p))
            if mems:
                registry.register(
                    agent_id,
                    installation_path=str(child),
                    memory_files=mems,
                    display_name=agent_id,
                )

    new_agents = registry.scan_local()
    registry.update_local_stats()

    all_agents = registry.list_all()
    print("===== Agent 本地安装发现 (v1.3) =====")
    print("融合层 (OneDrive): {}".format(root))
    print("本机用户目录: {}".format(Path.home()))
    print()
    print("已登记 {} 个 Agent (本次新增 {} 个):".format(len(all_agents), len(new_agents)))
    print()

    for info in all_agents:
        agent_id = info.get("agent_id", "?")
        install = info.get("installation_path", "N/A")
        mems = info.get("memory_files", [])
        local_stats = info.get("local_stats", {})
        entries = local_stats.get("total_entries", 0)
        files = local_stats.get("file_count", 0)
        last_seen = info.get("last_seen", "N/A")
        print("  [{}{}]".format(agent_id, " *新*" if agent_id in new_agents else ""))
        print("    安装路径: {}".format(install))
        print("    本地文件: {} 个 / 解析出条目: {} 条".format(files, entries))
        for m in mems:
            print("      - {}".format(m))
        print("    最后活跃: {}".format(last_seen))
        print()

    print("Registry 已保存: {}".format(registry.registry_path))


def cmd_dashboard(args):
    """
    v1.3 Dashboard: 双层视图 (本地安装层 + OneDrive 融合层)
    """
    root = Path(args.root)
    registry = am.AgentRegistry(root=root)
    registry.update_local_stats()
    registry.update_memory_counts()

    agents = registry.list_all()
    if not agents:
        print("Registry 为空, 请先执行 discover")
        return

    print("===== Agent Memory Dashboard (v1.3 双层视图) =====")
    print("融合层: {}".format(root))
    print("Registry: {}".format(registry.registry_path))
    print()

    now = datetime.now(timezone.utc)
    stale_agents = []

    for info in agents:
        agent_id = info.get("agent_id", "?")
        install = info.get("installation_path", "N/A")
        local_stats = info.get("local_stats", {})
        local_files = local_stats.get("file_count", 0)
        local_entries = local_stats.get("total_entries", 0)
        local_size = local_stats.get("total_size", 0)
        local_last = local_stats.get("last_modified", "N/A")
        fused_private = info.get("fused_memory_count", 0)
        fused_shared = info.get("fused_shared_count", 0)
        last_seen = info.get("last_seen", "N/A")

        # 本地层
        print("Agent: {}".format(agent_id))
        print("  ┌─ 本地层 (安装目录)")
        print("  │  路径: {}".format(install))
        print("  │  记忆文件: {} 个 | 条目: {} 条 | 大小: {} 字节".format(
            local_files, local_entries, local_size
        ))
        if local_last != "N/A":
            try:
                ll_dt = datetime.fromisoformat(local_last.replace("Z", "+00:00"))
                days = (now - ll_dt).days
                if days > 7:
                    stale_agents.append((agent_id, days))
                    print("  │  最后修改: {} ({} 天前) ⚠".format(local_last, days))
                else:
                    print("  │  最后修改: {} ({} 天前)".format(local_last, days))
            except:
                print("  │  最后修改: {}".format(local_last))
        print("  │")
        print("  └─ 融合层 (OneDrive\\agent_{})".format(agent_id))
        print("     私有: {} 条 | 共享: {} 条".format(fused_private, fused_shared))
        print("     最后活跃: {}".format(last_seen))
        print()

    if stale_agents:
        print("提示: 以下 Agent 本地文件超过 7 天未修改:")
        for aid, days in stale_agents:
            print("  - {} ({} 天前)".format(aid, days))


def cmd_extract(args):
    """
    v1.3 新增: 从 Agent 本地记忆文件提取到 OneDrive 融合层

    流程:
      1. 读取 LocalMemoryParser 解析的条目
      2. 写入 OneDrive\\AgentMemory\\agent_<id>\\memory_private.md
      3. 追加 front matter, 标注 source_format / source_device
    """
    root = Path(args.root)
    agent_id = args.agent
    dry_run = args.dry_run

    registry = am.AgentRegistry(root=root)

    # 确定要提取的本地文件
    if args.local_file:
        local_files = list(args.local_file)
    else:
        local_files = registry.get_agent_memory_files(agent_id)
        if not local_files:
            # 实时扫描一次
            registry.scan_local()
            local_files = registry.get_agent_memory_files(agent_id)

    print("===== 提取本地记忆 → 融合层 (v1.3) =====")
    print("Agent: {}".format(agent_id))
    print("融合层: {}".format(root))
    print("模式: {}".format("DRY-RUN (不实际写入)" if dry_run else "实际写入"))
    print()
    print("待提取本地文件 ({} 个):".format(len(local_files)))
    for f in local_files:
        print("  - {}".format(f))
    print()

    if dry_run:
        # 仅解析
        parser = am.LocalMemoryParser()
        total = 0
        for f in local_files:
            entries = parser.parse_file(Path(f))
            print("  {} → {} 条".format(f, len(entries)))
            total += len(entries)
        print()
        print("DRY-RUN 完成: 共 {} 条将写入".format(total))
        return

    # 实际提取
    result = am.extract_local_to_fused(
        agent_id=agent_id,
        root=root,
        local_files=local_files,
        registry=registry,
    )

    print("结果:")
    print("  已提取: {} 条".format(result.get("extracted", 0)))
    print("  跳过: {} 个文件".format(result.get("skipped", 0)))
    print("  错误: {} 条".format(len(result.get("errors", []))))
    print("  写入文件: {}".format(result.get("fused_file", "N/A")))

    if result.get("errors"):
        print()
        print("错误明细:")
        for e in result["errors"]:
            print("  - {}".format(e))

    # 同步后刷新统计
    registry.update_memory_counts()
    print()
    print("融合层统计已刷新。下一步可执行:")
    print("  python memory_cli.py --root \"{}\" sync".format(root))
    print("  python memory_cli.py --root \"{}\" dashboard".format(root))


def cmd_flush(args):
    """
    批量落盘 SessionFlusher buffer 中的待写条目
    """
    root = Path(args.root)
    agent_dir = find_agent_dir(root, args.agent)

    flusher = am.SessionFlusher(memory_root=agent_dir)
    result = flusher.flush(dry_run=args.dry_run)

    print("===== Session Flush =====")
    print("Agent: {}".format(args.agent))
    print("Memory Root: {}".format(agent_dir))
    if args.dry_run:
        print("模式: 试运行 (不实际写入)")
    print()
    print("总计: {} 条".format(result["total"]))
    print("已落盘: {} 条".format(result["written"]))
    print("已跳过: {} 条".format(result["skipped"]))

    if result["skipped_entries"]:
        print()
        print("跳过的条目:")
        for entry in result["skipped_entries"]:
            print("  - {}  原因: {}".format(
                entry.get("content", "")[:60], entry.get("reason", "")
            ))


# ---------------------------------------------------------------------------
# 新增命令: full-sync / redetect
# ---------------------------------------------------------------------------

def cmd_full_sync(args):
    """
    完整同步流程: 发现 Agent → 提取记忆 → 融合 → 写回各 Agent
    """
    from sync_engine import SyncEngine

    def cli_progress(msg):
        print("  {}".format(msg))

    print("=== 完整同步 ===")
    print()

    engine = SyncEngine(on_progress=cli_progress)
    report = engine.run()

    print()
    print(report.summary_text())


def cmd_redetect(args):
    """
    重新检测本机 Agent 路径（清除缓存）
    """
    from agent_memory import detect_agents, get_config

    print("=== 重新检测 Agent ===")
    print()

    config = get_config()
    detected = detect_agents(config, force_redetect=True)

    if not detected:
        print("未发现任何 Agent")
        print()
        print("请检查:")
        print("  1. Agent 是否已安装")
        print("  2. config.json 中的 agent_detection 配置是否正确")
        print("  3. 可以在 agent_overrides 中手动指定路径")
        return

    print("发现 {} 个 Agent:".format(len(detected)))
    print()

    for agent_id, info in detected.items():
        source = info.get("source", "auto")
        path = info.get("path", "")
        files = info.get("memory_files", [])
        print("  {} [{}]".format(agent_id, source))
        print("    路径: {}".format(path))
        print("    文件: {} 个".format(len(files)))
        if files:
            for f in files[:3]:
                print("      - {}".format(f))
            if len(files) > 3:
                print("      ... 还有 {} 个".format(len(files) - 3))
        print()


if __name__ == "__main__":
    main()
