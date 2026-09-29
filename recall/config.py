"""Configuration: agent memory sources and local index location.

Invariants:
* These source directories are READ-ONLY; we never write into them.
* Index + logs live under %LOCALAPPDATA%\\recall-memory (never on OneDrive).
* An optional portable `recall_config.json` next to this package may OVERRIDE
  `sources` / `sync_interval_minutes` for cross-device tuning. If absent, the
  built-in SOURCES below are used.
"""

from __future__ import annotations

import json
import os
import sys

K_CONFIG = "recall_config.json"


def _expand(path: str) -> str:
    return os.path.expanduser(os.path.expandvars(path))


def default_db_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or _expand("~")
    return os.path.join(base, "recall-memory", "index.db")


# (agent_name, root_dir, glob) — every source file matching the glob under the
# root is treated as a memory source. Roots are scanned READ-ONLY; the glob is
# enforced by ingest._walk_md (T02). Matching rules:
#   * a pattern containing a path separator is matched against the file path
#     relative to the root; `**/` matches ZERO OR MORE directory segments, so
#     `**/*.md` also matches root-level files (required to keep trae at 47);
#   * a bare pattern (e.g. `MEMORY.md`) is matched against the file NAME only,
#     so a giant directory can be constrained to a single well-known file.
#   trae      : user_profile.md + projects/*/{topics,project_memory}.md
#               （topics.md 是每会话的干净中文摘要，信息密度最高）→ 47 files
#   dsh       : 只取 ~/.dsh/MEMORY.md（其余 248 个 md 几乎全在 node_modules/）
#   pi        : 修正 —— 真实 markdown 记忆是 ~/.pi/MEMORY.md（旧 ~/.pi/.workbuddy 不存在）；
#               现用 **/MEMORY.md 一并覆盖 ~/.pi/memory/MEMORY.md（"pi-web Memory"，
#               独立真实记忆）。~/.pi 目录极小（14 目录），递归 walk 无性能问题；
#               这样在"裸 glob 只查根层"的新语义下也不丢该文件。
#   dsh/workbuddy/codepilot/claude : 只取根层 MEMORY.md（裸 glob = 只查根层，绝不
#               递归 ~/.workbuddy 的 ~15 万文件——那是 95% 的同步耗时来源）
#   workbuddy : 你在用的 Agent；只取 ~/.workbuddy/MEMORY.md（该目录 raw ≈1.4 万 md，
#               经 EXCLUDE_DIRS 剪枝后 ≈7.4 千；全靠 glob 约束，绝不能全收）
#   codepilot : 只取 ~/.codepilot/MEMORY.md
#   claude    : 选择性纳入（D4）—— 只取 ~/.claude/MEMORY.md，不收 1209 个散落 md；
#               当前该文件不存在 → 0 命中（保留条目，缺失时静默跳过）
SOURCES = [
    ("trae", "~/.trae-cn/memory", "**/*.md"),
    ("dsh", "~/.dsh", "MEMORY.md"),
    ("pi", "~/.pi", "**/MEMORY.md"),
    ("workbuddy", "~/.workbuddy", "MEMORY.md"),
    ("codepilot", "~/.codepilot", "MEMORY.md"),
    ("claude", "~/.claude", "MEMORY.md"),
]

DEFAULT_SYNC_INTERVAL_MINUTES = 180  # 3 hours

# Directories always skipped while walking a root.
EXCLUDE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".sync_backups", ".backups", ".archive", ".logs", "skills",
    "binaries", "cache",
}

# Filesystem safety caps (Phase 0 pragmatic defaults).
MAX_FILE_BYTES = 512 * 1024       # read at most 512KB per file
MAX_SECTIONS_PER_FILE = 200       # entries per file
MAX_SECTION_CHARS = 4000          # retained body length per entry
# NOTE (T05, 2026-09-22): paragraph-level chunking (chunk cap 1200 / target 200 /
# `h2 › h3` prefix) was implemented and measured against the frozen golden set.
# It cut DOC (0.120 -> 0.061, good = fewer look-alike previews) but REGRESSED
# recall@5 (52.0% -> 30.0%) and MRR (0.319 -> 0.200); every parameter variant
# tried failed the pre-registered keep gate, so it was ROLLED BACK. See
# recall/eval/REPORT.md and the negative-result note. MAX_SECTION_CHARS stays 4000.
MAX_ENTRIES_PER_SOURCE = 3000     # T02: hard cap of entries indexed per source,
                                  # so a runaway directory can never blow up the
                                  # index. Sources that hit the cap are reported
                                  # in the run summary (`truncated`) and logged
                                  # (see runner.sync_once / [SYNC DEGRADED]).

loaded: dict | None = None


def _config_file_path() -> str:
    """Portable config sits NEXT TO the program.

    Frozen (PyInstaller) -> beside the .exe, so a OneDrive-hosted exe picks up
    the editable recall_config.json next to it on every device.
    Source mode -> beside this package.
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, K_CONFIG)


def load_portable_config() -> dict:
    """Load the optional portable config next to the package (OneDrive)."""
    global loaded
    if loaded is not None:
        return loaded
    loaded = {}
    try:
        with open(_config_file_path(), "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError):
        loaded = {}
    return loaded


def sources() -> list[tuple[str, str, str]]:
    cfg = load_portable_config()
    extra = cfg.get("sources")
    if isinstance(extra, list) and extra:
        return [(s["agent"], s["root"], s["glob"]) for s in extra
                if isinstance(s, dict) and "root" in s]
    return SOURCES


def sync_interval_minutes() -> int:
    cfg = load_portable_config()
    v = cfg.get("sync_interval_minutes")
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    return DEFAULT_SYNC_INTERVAL_MINUTES