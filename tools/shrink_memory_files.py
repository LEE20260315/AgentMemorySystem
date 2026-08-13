#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一次性体积治理工具：压缩/截断超大的 memory_private.md 与 memory_shared.md。

背景
----
现有 Agent 记忆文件（codebuddy/hermes/openclaw/codepilot）已膨胀到 1MB+，
同步管线从不调用压缩/过期服务（Phase 0.5 文档中的"死代码"问题）。
本工具提供一次性清理，并在 sync_engine 中接入体积控制后维持稳定。

策略（保守、可逆、可审计）
----
1. 备份原文件为 .bak_shrink_<timestamp>
2. 解析 front matter 条目（--- 包裹）
3. 完全重复条目：保留最新一条
4. 高度相似条目（前 80 字符相同 + 标签相同）：合并为一条
5. 排序：high > medium > low confidence；同 confidence 按时间倒序
6. 永久保留标签优先：core_identity / permanent / 用户身份
7. 按 max_lines 截断（默认 3000）
8. 输出详细报告

用法
----
    # 预览（不写入）
    python tools/shrink_memory_files.py --dry-run

    # 执行清理
    python tools/shrink_memory_files.py

    # 指定根目录
    python tools/shrink_memory_files.py --root C:/path/to/AgentMemorySystem

    # 只处理特定 Agent
    python tools/shrink_memory_files.py --agent hermes --agent codepilot

    # 自定义上限
    python tools/shrink_memory_files.py --max-lines 2000

通用性
------
不含硬编码路径，默认 root 为脚本上级目录。
"""
from __future__ import annotations

# v2.2.0: argparse 惰性导入（移入 main()）——PyInstaller 6.20 + Python 3.14 下
# 顶层 import argparse 无法被收集（--hidden-import 也无效），导致打包后
# sync_engine 静态导入 shrink_file 时 ImportError，体积控制降级兜底。
# 本模块作为库（shrink_file/parse_memory_entries/format_entry）使用时
# 不需要 argparse，仅在 CLI 入口（main）需要。
import re
import shutil
import sys
from pathlib import Path
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# 默认策略（与 phase_0_5_volume_control.md 保持一致）
# ---------------------------------------------------------------------------
DEFAULT_MAX_LINES = 3000
DEFAULT_MAX_SIZE_KB = 256

# 永久保留标签（即使超限也不删除）
NEVER_EXPIRE_TAGS = {"core_identity", "permanent", "用户身份", "用户明确指令"}

# Confidence 优先级映射
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


# ---------------------------------------------------------------------------
# Front matter 解析
# ---------------------------------------------------------------------------
FRONT_MATTER_RE = re.compile(
    r"^---\s*$\n(.*?)\n^---\s*$\n(.*?)(?=^---\s*$\n|\Z)",
    re.MULTILINE | re.DOTALL,
)


def parse_front_matter(fm_text: str) -> dict:
    """简单 YAML 解析（仅支持 key: value 与 key: [list] 两种）。"""
    data = {}
    for line in fm_text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # list 格式: ["a", "b"]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            items = [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]
            data[key] = items
        elif value.lower() in ("null", "none", "~"):
            data[key] = None
        else:
            # 去引号
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            data[key] = value
    return data


def parse_memory_entries(text: str) -> list:
    """解析 memory_*.md 文件，返回 [(front_matter_dict, body_text), ...]。"""
    entries = []
    # 第一个 --- 之前通常是 # 标题，跳过
    matches = FRONT_MATTER_RE.findall(text)
    for fm_text, body in matches:
        fm = parse_front_matter(fm_text)
        body = body.strip()
        entries.append((fm, body))
    return entries


def format_entry(fm: dict, body: str) -> str:
    """重新格式化为 front matter + body 字符串。"""
    lines = ["---"]
    for key in ["id", "agent_id", "timestamp", "source_device", "domain",
                "tags", "confidence", "conflict_with"]:
        if key not in fm:
            continue
        value = fm[key]
        if value is None:
            lines.append("{}: null".format(key))
        elif isinstance(value, list):
            items = ", ".join('"{}"'.format(s) for s in value)
            lines.append("{}: [{}]".format(key, items))
        else:
            lines.append("{}: {}".format(key, value))
    lines.append("---")
    lines.append(body)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 去重 / 合并
# ---------------------------------------------------------------------------
def entry_signature(fm: dict, body: str) -> tuple:
    """生成条目签名（用于完全重复检测）。

    完全重复定义：内容文本完全一致（去除空白后）。
    """
    normalized_body = re.sub(r"\s+", " ", body).strip().lower()
    return (normalized_body,)


def entry_similarity_key(fm: dict, body: str) -> tuple:
    """生成相似度分桶键（用于"近似重复"合并）。

    近似重复定义：前 80 字符 + tags 一致。
    这是保守的相似度判断，避免误合并语义不同的条目。
    """
    normalized = re.sub(r"\s+", " ", body).strip().lower()
    prefix = normalized[:80]
    tags = tuple(sorted(fm.get("tags", []) if isinstance(fm.get("tags"), list) else []))
    return (prefix, tags)


def entry_sort_key(fm: dict, body: str) -> tuple:
    """排序键：永久标签优先 → confidence → 时间倒序。

    Returns (priority_asc, confidence_rank_asc, timestamp_desc)
    """
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    has_permanent = bool(set(tags or []) & NEVER_EXPIRE_TAGS)

    confidence = fm.get("confidence", "medium")
    if isinstance(confidence, list):
        confidence = confidence[0] if confidence else "medium"
    conf_rank = CONFIDENCE_RANK.get(str(confidence).lower(), 3)

    # 永久标签 → priority 0 (最先保留)；其他 → 1
    priority = 0 if has_permanent else 1

    # 时间戳倒序：用负数无法，用字符串排序取反
    timestamp = fm.get("timestamp", "")
    # ISO 时间戳按字符串排序就是正序，倒序需要反向
    # 我们用 (优先级, conf_rank, -timestamp) 但 timestamp 是字符串
    # 用一个 trick：timestamp 越大越优先 → 排序键取 (priority, conf_rank, NEG(timestamp))
    # 实现：返回 (priority, conf_rank, timestamp) 然后整体 reverse=True
    # 但 priority 与 conf_rank 是升序，timestamp 是降序，需要分别处理
    return (priority, conf_rank, timestamp)


# ---------------------------------------------------------------------------
# 核心清理函数
# ---------------------------------------------------------------------------
def shrink_file(
    file_path: Path,
    max_lines: int = DEFAULT_MAX_LINES,
    max_size_kb: int = DEFAULT_MAX_SIZE_KB,
    dry_run: bool = False,
) -> dict:
    """清理单个记忆文件。

    Returns
    -------
    dict
        {file, before_lines, before_size_kb, after_lines, after_size_kb,
         total_entries, deduped, merged, truncated, backed_up, action, error}
    """
    result = {
        "file": str(file_path),
        "before_lines": 0,
        "before_size_kb": 0,
        "after_lines": 0,
        "after_size_kb": 0,
        "total_entries": 0,
        "deduped": 0,
        "merged": 0,
        "truncated": 0,
        "backed_up": False,
        "action": "skipped",
        "error": None,
    }

    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        result["error"] = "读取失败: {}".format(e)
        return result

    result["before_lines"] = len(text.splitlines())
    result["before_size_kb"] = len(text.encode("utf-8")) // 1024

    # 未超限：跳过
    if result["before_lines"] <= max_lines and result["before_size_kb"] <= max_size_kb:
        result["action"] = "ok"
        result["after_lines"] = result["before_lines"]
        result["after_size_kb"] = result["before_size_kb"]
        return result

    # 解析条目
    entries = parse_memory_entries(text)
    result["total_entries"] = len(entries)
    if not entries:
        # 无 front matter 条目（可能是纯文本记忆），保守跳过
        result["action"] = "skipped_no_entries"
        result["after_lines"] = result["before_lines"]
        result["after_size_kb"] = result["before_size_kb"]
        return result

    # 保留文件头（第一个 --- 之前的内容，如 # 标题）
    first_fm_match = re.search(r"^---\s*$\n", text, re.MULTILINE)
    header = text[:first_fm_match.start()] if first_fm_match else ""

    # Step 1: 完全重复去重
    seen_signatures = {}
    deduped_entries = []
    for fm, body in entries:
        sig = entry_signature(fm, body)
        if sig in seen_signatures:
            # 重复：保留时间更新的那条
            existing_idx = seen_signatures[sig]
            existing_fm, existing_body = deduped_entries[existing_idx]
            existing_ts = existing_fm.get("timestamp", "")
            current_ts = fm.get("timestamp", "")
            if current_ts > existing_ts:
                deduped_entries[existing_idx] = (fm, body)
            result["deduped"] += 1
        else:
            seen_signatures[sig] = len(deduped_entries)
            deduped_entries.append((fm, body))

    # Step 2: 相似度合并（前 80 字符 + tags）
    sim_buckets = {}
    merged_entries = []
    for fm, body in deduped_entries:
        key = entry_similarity_key(fm, body)
        if key in sim_buckets:
            # 相似：合并（保留更新的一条，记录合并数）
            existing_idx = sim_buckets[key]
            existing_fm, existing_body = merged_entries[existing_idx]
            existing_ts = existing_fm.get("timestamp", "")
            current_ts = fm.get("timestamp", "")
            if current_ts > existing_ts:
                merged_entries[existing_idx] = (fm, body)
            result["merged"] += 1
        else:
            sim_buckets[key] = len(merged_entries)
            merged_entries.append((fm, body))

    # Step 3: 排序（永久标签优先 → high > medium > low → 时间倒序）
    # 排序键：(priority, conf_rank, timestamp)
    # 目标：priority 升序（永久在前）、conf_rank 升序（high 在前）、timestamp 降序（新在前）
    # v2.0.3 简化：用单次 sorted + reverse=True 实现
    #   对 priority 和 conf_rank 取反（max - val），使 reverse=True 后顺序正确
    sorted_entries = sorted(merged_entries,
        key=lambda e: (
            1 - entry_sort_key(e[0], e[1])[0],   # priority: 0=永久→1, 1=普通→0
            3 - entry_sort_key(e[0], e[1])[1],   # conf_rank: 0=high→3, 3=unknown→0
            entry_sort_key(e[0], e[1])[2],        # timestamp: 原样（reverse 后降序）
        ),
        reverse=True
    )

    # Step 4: 截断到 max_lines（保留永久条目）
    # 先估计每条目平均行数
    avg_lines_per_entry = max(3, result["before_lines"] // max(1, len(entries)))
    safe_count = max(1, max_lines // avg_lines_per_entry)

    if len(sorted_entries) > safe_count:
        result["truncated"] = len(sorted_entries) - safe_count
        sorted_entries = sorted_entries[:safe_count]

    # 重新构建文件
    new_text = header
    for fm, body in sorted_entries:
        new_text += format_entry(fm, body) + "\n\n---\n\n"
    # 去掉末尾多余的 ---
    new_text = re.sub(r"\n*---\s*\n*$", "\n", new_text)
    new_text = new_text.rstrip() + "\n"

    result["after_lines"] = len(new_text.splitlines())
    result["after_size_kb"] = len(new_text.encode("utf-8")) // 1024

    # 验证：如果压缩后仍超限（行数或体积），按更严格的上限再截断
    max_iterations = 5  # 防止极端情况死循环
    while (result["after_lines"] > max_lines
           or result["after_size_kb"] > max_size_kb) and max_iterations > 0:
        # 计算需要砍掉多少行
        if result["after_size_kb"] > max_size_kb:
            # 体积超限：按比例缩减
            ratio = max_size_kb / max(1, result["after_size_kb"])
            target_lines = int(result["after_lines"] * ratio * 0.95)  # 留 5% 余量
        else:
            # 行数超限
            target_lines = max_lines
        if target_lines >= result["after_lines"]:
            break

        # v2.0 修复：在条目边界截断，不切断 front matter 块
        # 查找不超过 target_lines 的最后一个完整条目边界（^---\s*$ 行）
        all_lines = new_text.splitlines()
        cut_line = target_lines
        # 从 target_lines 往前找最近的 ^---\s*$ 行（条目结束标记）
        for i in range(min(target_lines, len(all_lines)) - 1, 0, -1):
            if re.match(r"^---\s*$", all_lines[i]):
                cut_line = i + 1  # 保留到这一行（包含 ---）
                break
        new_lines = all_lines[:cut_line]
        new_text = "\n".join(new_lines) + "\n"
        result["after_lines"] = len(new_text.splitlines())
        result["after_size_kb"] = len(new_text.encode("utf-8")) // 1024
        result["action"] = "force_truncated"
        max_iterations -= 1
    else:
        if result["action"] != "force_truncated":
            result["action"] = "shrunk"

    if dry_run:
        return result

    # 备份
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = file_path.with_suffix(
        file_path.suffix + ".bak_shrink_{}".format(timestamp_str)
    )
    try:
        shutil.copy2(str(file_path), str(backup_path))
        result["backed_up"] = True
    except Exception as e:
        result["error"] = "备份失败: {}".format(e)
        return result

    # 写入
    try:
        file_path.write_text(new_text, encoding="utf-8")
    except Exception as e:
        result["error"] = "写入失败: {}".format(e)
        # 回滚
        try:
            shutil.copy2(str(backup_path), str(file_path))
        except Exception:
            pass
        return result

    return result


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def find_target_files(root: Path, agents: list = None) -> list:
    """查找所有 agent_*/memory_private.md 与 memory_shared.md。"""
    data_dir = root / "data"
    if not data_dir.exists():
        return []

    targets = []
    for agent_dir in sorted(data_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        if not agent_dir.name.startswith("agent_"):
            continue
        agent_id = agent_dir.name[len("agent_"):]
        if agents and agent_id not in agents:
            continue
        for fname in ("memory_private.md", "memory_shared.md"):
            fp = agent_dir / fname
            if fp.exists():
                targets.append(fp)
    return targets


def main():
    import argparse  # v2.2.0: 惰性导入（见模块顶部注释）

    parser = argparse.ArgumentParser(
        description="一次性体积治理：压缩/截断超大的 memory_*.md 文件"
    )
    parser.add_argument(
        "--root", default=None,
        help="AgentMemorySystem 项目根目录（默认: 脚本上级目录）"
    )
    parser.add_argument(
        "--agent", action="append", default=[],
        help="只处理指定 Agent（可多次指定，如 --agent hermes --agent codepilot）"
    )
    parser.add_argument(
        "--max-lines", type=int, default=DEFAULT_MAX_LINES,
        help="最大行数上限（默认 {}）".format(DEFAULT_MAX_LINES)
    )
    parser.add_argument(
        "--max-size-kb", type=int, default=DEFAULT_MAX_SIZE_KB,
        help="最大体积 KB 上限（默认 {}）".format(DEFAULT_MAX_SIZE_KB)
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="试运行：只打印报告，不实际写入"
    )
    args = parser.parse_args()

    # 确定 root
    if args.root:
        root = Path(args.root).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parent.parent

    if not root.exists():
        print("错误: 项目根目录不存在: {}".format(root))
        sys.exit(1)

    print("=" * 60)
    print("AgentMemorySystem 体积治理工具")
    print("=" * 60)
    print("项目根: {}".format(root))
    print("模式: {}".format("DRY-RUN（试运行）" if args.dry_run else "EXECUTE（执行清理）"))
    print("上限: {} 行 / {} KB".format(args.max_lines, args.max_size_kb))
    if args.agent:
        print("限定 Agent: {}".format(", ".join(args.agent)))
    print()

    targets = find_target_files(root, args.agent or None)
    if not targets:
        print("未找到任何 memory_private.md / memory_shared.md 文件")
        sys.exit(0)

    print("扫描到 {} 个候选文件:".format(len(targets)))
    for fp in targets:
        size_kb = fp.stat().st_size // 1024
        line_count = len(fp.read_text(encoding="utf-8", errors="ignore").splitlines())
        flag = " ⚠️" if (line_count > args.max_lines or size_kb > args.max_size_kb) else " ✓"
        print("  {} [{}KB / {} 行]{}".format(
            fp.relative_to(root), size_kb, line_count, flag))
    print()

    # 执行清理
    results = []
    for fp in targets:
        print("处理: {}".format(fp.relative_to(root)))
        r = shrink_file(
            fp,
            max_lines=args.max_lines,
            max_size_kb=args.max_size_kb,
            dry_run=args.dry_run,
        )
        results.append(r)

        if r.get("error"):
            print("  ❌ 错误: {}".format(r["error"]))
        elif r["action"] == "ok":
            print("  ✓ 未超限，跳过")
        elif r["action"] == "skipped_no_entries":
            print("  ⚠ 无 front matter 条目，跳过")
        else:
            print("  {} {} → {} 条目, {} → {} 行, {} → {} KB".format(
                "📦" if not args.dry_run else "🔍",
                r["total_entries"],
                r["total_entries"] - r["deduped"] - r["merged"] - r["truncated"],
                r["before_lines"], r["after_lines"],
                r["before_size_kb"], r["after_size_kb"],
            ))
            print("    去重: {} | 合并: {} | 截断: {} | 备份: {}".format(
                r["deduped"], r["merged"], r["truncated"],
                "是" if r["backed_up"] else "否"))
        print()

    # 汇总
    print("=" * 60)
    print("汇总")
    print("=" * 60)
    total_before = sum(r["before_lines"] for r in results)
    total_after = sum(r["after_lines"] for r in results)
    total_deduped = sum(r["deduped"] for r in results)
    total_merged = sum(r["merged"] for r in results)
    total_truncated = sum(r["truncated"] for r in results)
    print("文件数: {}".format(len(results)))
    print("总行数: {} → {} (减少 {:.1%})".format(
        total_before, total_after,
        1 - total_after / max(1, total_before)
    ))
    print("去重: {} 条 | 合并: {} 条 | 截断: {} 条".format(
        total_deduped, total_merged, total_truncated))

    errors = [r for r in results if r.get("error")]
    if errors:
        print()
        print("错误:")
        for r in errors:
            print("  - {}: {}".format(r["file"], r["error"]))

    if args.dry_run:
        print()
        print("（试运行模式，未实际写入。移除 --dry-run 执行清理。）")


if __name__ == "__main__":
    main()
