"""
Agent 记忆写回适配器
==================
为每种 Agent 提供格式兼容的写回能力。

支持的 Agent:
- Claude: 创建 shared_from_agents.md 子文件 + 更新 MEMORY.md 索引
- Trae: 追加到 user_profile.md 的 ## Shared Knowledge 段
- Hermes: 追加到 MEMORY.md 末尾，用 § 分隔
"""

from __future__ import annotations

import json
import os
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent_memory import (
    MemoryEntry, content_hash, get_logger, get_config,
)
from safe_io import _pending_path, _safe_read_text, _safe_write_text


# ---------------------------------------------------------------------------
# 写回结果
# ---------------------------------------------------------------------------

@dataclass
class WriteBackResult:
    """写回结果统计"""
    agent_id: str
    target_path: str
    written: int
    skipped: int  # 去重跳过
    errors: list
    backup_path: Optional[str] = None
    pending: int = 0  # 因主文件锁定而写入 .pending 的文件数


# ---------------------------------------------------------------------------
# 同步标记格式（方案 2 升级版：显式 hash marker）
# ---------------------------------------------------------------------------
# 设计要点：
#   写入时统一输出 [sync:<id>|h:<content_hash>|src:<agent_id>]
#   这样 target 文件里的"自愈指纹"和 SyncState 里记的"原始内容 hash"
#   口径 1:1 一致，根本解决"hash 口径错位"。
#   老格式 [sync:<id>] (无 h:) 视为 legacy，reconcile 时保守处理。

def format_sync_marker(mem) -> str:
    """生成统一的同步标记字符串。
    mem: MemoryEntry
    """
    from agent_memory import content_hash
    h = content_hash(mem.content)
    return "[sync:{}|h:{}|src:{}]".format(mem.id, h, mem.agent_id)


def parse_sync_marker(text: str) -> dict:
    """从文本片段里捕捉 [sync:...] 系列标记。
    返回 None 或 dict{id,h,src}。
    支持老格式 [sync:<id>] (无 h: 无 src:)。
    """
    import re
    m = re.search(r"\[sync:([^\]]+)\]", text)
    if not m:
        return None
    raw = m.group(1)
    parts = {} if "|" not in raw else dict(
        (k.strip(), v.strip())
        for k, v in (seg.split(":", 1) for seg in raw.split("|") if ":" in seg)
    )
    # 兼容老格式：[sync:<id>]
    if "id" not in parts:
        parts["id"] = raw.strip()
    return parts


def strip_sync_markers(text: str) -> str:
    """剥离文本里的 [sync:...] 同步标记，防止跨 Agent 回声污染。

    记忆内容本身不应携带 sync 标记：如果 content 里含 [sync:...]，
    说明它是从某个 Agent 同步文件里提取出的"回声"，再次写入会叠加新
    sync 标记，造成嵌套污染雪崩（observed: trae user_profile.md
    膨胀到 52MB / 100 个重复段 / 3 万个嵌套 marker）。此函数在写入前
    统一脱敏。
    """
    import re
    cleaned = re.sub(r"\[sync:[^\]]*\]", "", text)
    # 清理脱敏后残留的空列表项 "- " (原本是 "- [sync:xxx] 内容")
    cleaned = re.sub(r"(?m)^[ \t]*-[ \t]*$", "", cleaned)
    # 压缩连续空格 (非换行)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    # 压缩连续空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# 去重状态管理
# ---------------------------------------------------------------------------

class SyncState:
    """管理同步去重状态（.sync_state.json）"""

    def __init__(self, state_path: Path = None):
        if state_path is None:
            # 默认存放在数据根目录（兼容开发和打包模式）
            from safe_io import get_data_root
            state_path = get_data_root() / ".sync_state.json"
        self.state_path = Path(state_path)
        self.state = self._load()

    def _load(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except PermissionError:
            # 文件被其他进程锁定，静默跳过
            pass

    def is_duplicate(self, agent_id: str, content: str) -> bool:
        h = content_hash(content)
        agent_state = self.state.get(agent_id, {})
        return h in agent_state

    def mark_written(self, agent_id: str, content: str):
        h = content_hash(content)
        if agent_id not in self.state:
            self.state[agent_id] = {}
        self.state[agent_id][h] = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # 自愈机制（方案 2 升级版）：显式 hash marker + legacy 保守模式
    # ------------------------------------------------------------------
    def reconcile_with_target_hashes(
        self,
        agent_id: str,
        actual_hashes: set,
        legacy_count: int = 0,
        target_file_present: bool = True,
    ) -> dict:
        """把 SyncState[agent_id] 与目标文件中实际存在的 hash 对齐。

        关键设计：以“显式 hash marker (新格式)”为唯一真相源。
        老格式(无 h: 字段)的 marker 不参与比对,只记数。

        保守模式：
          - 目标文件不存在 → 不删 state。
          - 只有 legacy 没有新 marker → 不删 state。
          这避免“hermes 永远写入 0”现象的成因。

        返回: {removed, kept, actual_only, conservative}
        """
        tracked = set(self.state.get(agent_id, {}).keys())
        result = {
            "removed": 0,
            "kept": len(tracked),
            "actual_only": 0,
            "conservative": False,
        }

        # 保守模式 1：目标文件不存在
        if not target_file_present:
            result["conservative"] = True
            return result

        # 保守模式 2：只有 legacy marker 没有新 marker
        if legacy_count > 0 and not actual_hashes:
            result["conservative"] = True
            return result

        # 正常模式：删除 tracked 中孤儿，保留交集
        to_remove = tracked - actual_hashes
        result["actual_only"] = len(actual_hashes - tracked)
        if agent_id in self.state and to_remove:
            for h in to_remove:
                self.state[agent_id].pop(h, None)
        result["removed"] = len(to_remove)
        result["kept"] = len(tracked & actual_hashes)

        if (
            self.state.get(agent_id) is not None
            and not self.state[agent_id]
        ):
            self.state.pop(agent_id, None)
        return result

    def bulk_known_hashes(self, agent_id: str) -> set:
        """返回 SyncState[agent_id] 跟踪的全部 hash 集合（用于比对 / 调试）。"""
        return set(self.state.get(agent_id, {}).keys())


# ---------------------------------------------------------------------------
# 备份管理
# ---------------------------------------------------------------------------

def backup_file(file_path: Path, backup_dir: Path) -> Path:
    """
    备份文件到指定目录

    Parameters
    ----------
    file_path : Path
        要备份的文件
    backup_dir : Path
        备份目标目录

    Returns
    -------
    Path
        备份文件路径
    """
    if not file_path.exists():
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    # 用原文件名 + .bak 后缀
    safe_name = file_path.name.replace("/", "_").replace("\\", "_")
    backup_path = backup_dir / "{}.bak".format(safe_name)

    import shutil
    shutil.copy2(str(file_path), str(backup_path))
    return backup_path


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------

class BaseMemoryWriter(ABC):
    """写回适配器基类"""

    def __init__(self, sync_state: SyncState = None):
        self.sync_state = sync_state or SyncState()
        self.logger = get_logger()

    @abstractmethod
    def write(
        self,
        agent_id: str,
        target_path: Path,
        memories: list[MemoryEntry],
        backup_dir: Path = None
    ) -> WriteBackResult:
        """
        将共享记忆写回 Agent 的本地文件

        Parameters
        ----------
        agent_id : str
            Agent ID
        target_path : Path
            Agent 记忆目录路径
        memories : list[MemoryEntry]
            要写入的共享记忆列表
        backup_dir : Path, optional
            备份目录

        Returns
        -------
        WriteBackResult
            写回结果
        """
        pass

    # ------------------------------------------------------------------
    # 自愈钩子（方案 2）：每个 writer 负责告诉 SyncState
    # "目标文件里实际存在哪些我曾经写入过的内容指纹"
    # ------------------------------------------------------------------
    def extract_target_info(
        self,
        agent_id: str,
        target_path: Path,
    ) -> dict:
        """扫描目标文件，返回自愈 reconcile 所需的全部信息。

        新方法（v2），提供比 extract_hashes_in_target 更丰富的信号：
          - hashes: set[str]  解析得到的 content_hash 集合
          - legacy: int       仅有 legacy [sync:<id>] 格式的条目数
          - file_present: bool 目标文件（或子文件）是否存在

        子类只需覆盖此方法；老方法 extract_hashes_in_target 仍存在以兼容
        早期调用方，等价于本方法的 hashes 字段。
        """
        try:
            text, candidates = self._read_target_text_safe(target_path)
        except Exception as e:
            self.logger.warning(
                "extract_target_info 读目标失败({}): {}".format(type(self).__name__, e)
            )
            return {"hashes": set(), "legacy": 0, "file_present": False}

        if not text or not candidates:
            return {"hashes": set(), "legacy": 0, "file_present": False}

        # 默认实现走与 extract_hashes_via_sync_marker 同样的"h: 字段"策略
        hashes = self._parse_marker_hashes(text)
        legacy = self._count_legacy_markers(text) - len(hashes)
        return {"hashes": hashes, "legacy": legacy, "file_present": True}

    def extract_hashes_in_target(
        self,
        agent_id: str,
        target_path: Path,
    ) -> set:
        """旧接口，保持向后兼容。直接转调 extract_target_info。

        Returns
        -------
        set[str]: 目标文件里仍可识别出的"同步产物"hash 集合。
        """
        try:
            return self.extract_target_info(agent_id, target_path).get("hashes", set())
        except Exception as e:
            self.logger.warning(
                "extract_hashes_in_target 失败({}): {}".format(type(self).__name__, e)
            )
            return set()

    # ----------------------- helpers -----------------------
    def _read_target_text_safe(self, target_path: Path) -> tuple:
        """读取目标路径下的候记忆文件。

        返回 (combined_text, candidates)：
          - candidates 为找到的文件列表，子类可作精细处理。
          - combined_text 是所有候选内容的拼接。
        """
        candidates = []
        if target_path.is_file():
            candidates.append(target_path)
        elif target_path.is_dir():
            for name in self._candidate_filenames():
                p = target_path / name
                if p.exists() and p.is_file():
                    candidates.append(p)
        if not candidates:
            return ("", [])
        chunks = []
        for fp in candidates:
            try:
                t = _safe_read_text(fp, default="")
            except Exception:
                continue
            if t:
                chunks.append(t)
        return ("\n".join(chunks), candidates)

    def _candidate_filenames(self) -> tuple:
        return (
            "MEMORY.md", "memory.md", "memories.md",
            "user_profile.md", "shared.md", "memory_shared.md",
            "shared_from_agents.md",
        )

    def _parse_marker_hashes(self, text: str) -> set:
        import re
        hashes = set()
        if not text:
            return hashes
        for m in re.finditer(r"\[sync:([^\]]+)\]", text):
            raw = m.group(1)
            if "h:" in raw:
                for s in raw.split("|"):
                    s = s.strip()
                    if s.startswith("h:"):
                        hv = s[2:].strip()
                        if hv:
                            hashes.add(hv)
        return hashes

    def _count_legacy_markers(self, text: str) -> int:
        """统计所有 [sync:...] marker 总数（含已迁移为含 h: 的）。"""
        import re
        return len(re.findall(r"\[sync:[^\]]+\]", text or ""))

    # ------------------------------------------------------------------
    # 污染检测与自愈（通用）：防止回声污染雪崩
    #
    # 背景：跨 Agent 同步时，若记忆内容本身含 [sync:...] 标记，
    # 原样写回会叠加新 marker，多次同步后文件膨胀到几十 MB、
    # 含数万个嵌套标记。此模块提供通用检测+自动备份重建能力，
    # 任意 writer 子类均可调用。
    # ------------------------------------------------------------------
    POLLUTION_SIZE_THRESHOLD = 5 * 1024 * 1024  # 5MB
    POLLUTION_MARKER_THRESHOLD = 1000  # sync marker 数量阈值

    def _detect_pollution(self, text: str) -> dict:
        """检测目标文本是否已被回声污染。

        Returns
        -------
        dict:
            polluted: bool   — 是否污染
            reason: str      — 污染原因（用于日志）
            marker_count: int — sync marker 总数
            size: int        — 文本字节数
        """
        if not text:
            return {"polluted": False, "reason": "", "marker_count": 0, "size": 0}
        size = len(text.encode("utf-8", errors="ignore"))
        marker_count = self._count_legacy_markers(text)
        polluted = False
        reason = ""
        if size > self.POLLUTION_SIZE_THRESHOLD:
            polluted = True
            reason = "文件超 5MB ({}KB)".format(size // 1024)
        elif marker_count > self.POLLUTION_MARKER_THRESHOLD:
            polluted = True
            reason = "sync marker 超阈值 ({})".format(marker_count)
        return {
            "polluted": polluted,
            "reason": reason,
            "marker_count": marker_count,
            "size": size,
        }

    def _repair_polluted_file(
        self,
        file_path: Path,
        text: str,
        clean_prefix: str = "",
        section_header: str = "",
        sync_state: "SyncState" = None,
        agent_id: str = "",
    ) -> str:
        """备份污染文件并重建干净版本。

        策略：
        1. 备份原文件到 ``<file>.bak_polluted_<timestamp>``
        2. 保留 section_header 之前的"本体内容"（用户画像等），
           丢弃所有已被污染的同步段
        3. 若提供 clean_prefix，用它作为重建文件的开头
        4. 返回重建后的干净文本

        Parameters
        ----------
        file_path : Path
            被污染的文件路径
        text : str
            文件当前内容
        clean_prefix : str
            重建文件的干净前缀（如 "# Trae User Profile\\n\\n"）
        section_header : str
            段头标记（如 "## Shared Knowledge"）。
            若提供，保留首个段头之前的本体，丢弃段头之后所有污染内容，
            再追加一个干净的空段头。
        """
        import time
        ts = time.strftime("%Y%m%d_%H%M%S")
        bak_path = file_path.parent / "{}.bak_polluted_{}".format(file_path.name, ts)
        try:
            import shutil
            shutil.copy2(str(file_path), str(bak_path))
            self.logger.warning(
                "污染自愈: 备份 {} → {} ({}KB)".format(
                    file_path.name, bak_path.name,
                    len(text.encode("utf-8", errors="ignore")) // 1024,
                )
            )
        except Exception as e:
            self.logger.warning("污染自愈: 备份失败 {}: {}".format(bak_path, e))

        # 重建干净内容
        if section_header and section_header in text:
            # 保留首个 section_header 之前的本体
            prefix = text.split(section_header, 1)[0].rstrip()
            if clean_prefix:
                rebuilt = clean_prefix.rstrip() + "\n\n" + section_header + "\n"
            elif prefix:
                rebuilt = prefix + "\n\n" + section_header + "\n"
            else:
                rebuilt = section_header + "\n"
        elif clean_prefix:
            rebuilt = clean_prefix.rstrip() + "\n"
        else:
            # 无法确定本体，重建最小骨架
            rebuilt = "# Memory (rebuilt after pollution)\n\n"
            if section_header:
                rebuilt += section_header + "\n"

        # 写回
        try:
            _safe_write_text(file_path, rebuilt)
            self.logger.info(
                "污染自愈: {} 重建完成 ({} → {}KB)".format(
                    file_path.name,
                    len(text.encode("utf-8", errors="ignore")) // 1024,
                    len(rebuilt.encode("utf-8", errors="ignore")) // 1024,
                )
            )
        except Exception as e:
            self.logger.error("污染自愈: 写回失败 {}: {}".format(file_path, e))
            return text  # 写回失败时返回原文，不丢失数据

        # 自愈成功后清除该 agent 的 SyncState（文件内容已变，旧 hash 全部无效）
        if sync_state and agent_id:
            if agent_id in sync_state.state:
                old_count = len(sync_state.state[agent_id])
                del sync_state.state[agent_id]
                sync_state.save()
                self.logger.info(
                    "污染自愈: 清除 agent {} sync_state ({} 条旧 hash)".format(
                        agent_id, old_count))

        return rebuilt


    def _extract_hashes_via_sync_marker(
        self,
        agent_id: str,
        target_path: Path,
    ) -> set:
        """通用启发式（方案 2 升级版）：直接解析文件中所有
        ``[sync:...|h:...]`` marker，提取 ``h:`` 字段。

        作为"未知 agent"最后的兜底：子类未覆盖
        ``extract_hashes_in_target`` 时使用。为保证写入 marker 与 state
        hash 口径一致，这里只解析正经 marker。

        老格式（无 h: 字段）将被忽略并记警告，等待下次完整同步重写。
        """
        import re
        candidates = []
        if target_path.is_file():
            candidates.append(target_path)
        elif target_path.is_dir():
            for name in (
                "MEMORY.md", "memory.md", "memories.md",
                "user_profile.md", "shared.md", "memory_shared.md",
                "shared_from_agents.md",
            ):
                p = target_path / name
                if p.exists() and p.is_file():
                    candidates.append(p)
        hashes = set()
        legacy_count = 0
        for fp in candidates:
            try:
                text = _safe_read_text(fp, default="")
            except Exception:
                continue
            if not text:
                continue
            for m in re.finditer(r"\[sync:([^\]]+)\]", text):
                raw = m.group(1)
                if "h:" in raw:
                    parts = [s.strip() for s in raw.split("|") if ":" in s]
                    hv = next(
                        (s.split(":", 1)[1].strip() for s in parts if s.startswith("h:")),
                        None,
                    )
                    if hv:
                        hashes.add(hv)
                else:
                    legacy_count += 1
        if legacy_count:
            self.logger.info(
                "通用钩子({}): 检测到 {} 条 legacy 同步条目（无 h: 字段），保守保留 state".format(
                    agent_id, legacy_count
                )
            )
        return hashes

    def _do_backup(self, file_path: Path, backup_dir: Path) -> Optional[str]:
        if backup_dir and file_path.exists():
            bp = backup_file(file_path, backup_dir)
            if bp:
                self.logger.info("备份: {} → {}".format(file_path, bp))
                return str(bp)
        return None


# ---------------------------------------------------------------------------
# 独立污染检测/自愈函数（供提取阶段 agent_memory.py 调用，无需 writer 实例）
# ---------------------------------------------------------------------------

POLLUTION_SIZE_THRESHOLD = BaseMemoryWriter.POLLUTION_SIZE_THRESHOLD
POLLUTION_MARKER_THRESHOLD = BaseMemoryWriter.POLLUTION_MARKER_THRESHOLD


def detect_pollution(text: str) -> dict:
    """独立函数版：检测目标文本是否已被回声污染。"""
    if not text:
        return {"polluted": False, "reason": "", "marker_count": 0, "size": 0}
    size = len(text.encode("utf-8", errors="ignore"))
    import re
    marker_count = len(re.findall(r"\[sync:[^\]]+\]", text or ""))
    polluted = False
    reason = ""
    if size > POLLUTION_SIZE_THRESHOLD:
        polluted = True
        reason = "文件超 5MB ({}KB)".format(size // 1024)
    elif marker_count > POLLUTION_MARKER_THRESHOLD:
        polluted = True
        reason = "sync marker 超阈值 ({})".format(marker_count)
    return {
        "polluted": polluted,
        "reason": reason,
        "marker_count": marker_count,
        "size": size,
    }


def repair_polluted_file(
    file_path: Path,
    text: str,
    clean_prefix: str = "",
    section_header: str = "",
    sync_state: "SyncState" = None,
    agent_id: str = "",
) -> str:
    """独立函数版：备份污染文件并重建干净版本。"""
    import time
    import shutil
    logger = get_logger()
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak_path = file_path.parent / "{}.bak_polluted_{}".format(file_path.name, ts)
    try:
        shutil.copy2(str(file_path), str(bak_path))
        logger.warning(
            "污染自愈(独立): 备份 {} → {} ({}KB)".format(
                file_path.name, bak_path.name,
                len(text.encode("utf-8", errors="ignore")) // 1024,
            )
        )
    except Exception as e:
        logger.warning("污染自愈(独立): 备份失败 {}: {}".format(bak_path, e))

    # 重建干净内容
    if section_header and section_header in text:
        prefix = text.split(section_header, 1)[0].rstrip()
        if clean_prefix:
            rebuilt = clean_prefix.rstrip() + "\n\n" + section_header + "\n"
        elif prefix:
            rebuilt = prefix + "\n\n" + section_header + "\n"
        else:
            rebuilt = section_header + "\n"
    elif clean_prefix:
        rebuilt = clean_prefix.rstrip() + "\n"
    else:
        rebuilt = "# Memory (rebuilt after pollution)\n\n"
        if section_header:
            rebuilt += section_header + "\n"

    try:
        _safe_write_text(file_path, rebuilt)
        logger.info(
            "污染自愈(独立): {} 重建完成 ({} → {}KB)".format(
                file_path.name,
                len(text.encode("utf-8", errors="ignore")) // 1024,
                len(rebuilt.encode("utf-8", errors="ignore")) // 1024,
            )
        )
    except Exception as e:
        logger.error("污染自愈(独立): 写回失败 {}: {}".format(file_path, e))
        return text

    # 自愈成功后清除该 agent 的 SyncState
    if sync_state and agent_id:
        if agent_id in sync_state.state:
            old_count = len(sync_state.state[agent_id])
            del sync_state.state[agent_id]
            sync_state.save()
            logger.info(
                "污染自愈(独立): 清除 agent {} sync_state ({} 条旧 hash)".format(
                    agent_id, old_count))

    return rebuilt


# ---------------------------------------------------------------------------
# Claude Writer
# ---------------------------------------------------------------------------

class ClaudeMemoryWriter(BaseMemoryWriter):
    """
    Claude 记忆写回器

    Claude 的 MEMORY.md 是索引文件，不能直接追加内容。
    写回策略：
    1. 创建 shared_from_agents.md 子文件（带 YAML front matter）
    2. 在 MEMORY.md 索引中添加链接
    """

    def write(
        self,
        agent_id: str,
        target_path: Path,
        memories: list[MemoryEntry],
        backup_dir: Path = None
    ) -> WriteBackResult:
        result = WriteBackResult(
            agent_id=agent_id,
            target_path=str(target_path),
            written=0,
            skipped=0,
            errors=[]
        )

        # Claude 的 target_path 可能是 .claude/projects 或具体的项目 memory 目录
        # 需要找到所有项目的 memory 目录
        memory_dirs = self._find_memory_dirs(target_path)

        if not memory_dirs:
            result.errors.append("未找到 Claude 记忆目录")
            return result

        # 过滤去重
        new_memories = []
        for mem in memories:
            if not self.sync_state.is_duplicate(agent_id, mem.content):
                new_memories.append(mem)
            else:
                result.skipped += 1

        if not new_memories:
            self.logger.info("Claude: 所有记忆已存在，跳过写入")
            return result

        # 格式化内容
        content = self._format_memories(new_memories)

        # 写入每个项目的 memory 目录
        for mem_dir in memory_dirs:
            try:
                # 备份
                shared_file = mem_dir / "shared_from_agents.md"
                if backup_dir:
                    result.backup_path = self._do_backup(shared_file, backup_dir)
                    # 也备份 MEMORY.md
                    self._do_backup(mem_dir / "MEMORY.md", backup_dir)

                # 写入 shared_from_agents.md（用 _safe_write_text 绕过文件锁）
                if not _safe_write_text(shared_file, content):
                    raise OSError("写入 shared_from_agents.md 失败（含重试+原子替换）")

                # 更新 MEMORY.md 索引
                index_path = mem_dir / "MEMORY.md"
                if index_path.exists():
                    index_content = _safe_read_text(index_path, default="")
                    link = "- [来自其他 Agent 的共享记忆](shared_from_agents.md) — 自动同步"
                    if "shared_from_agents" not in index_content:
                        index_content = index_content.rstrip() + "\n\n" + link + "\n"
                        if not _safe_write_text(index_path, index_content):
                            raise OSError("更新 MEMORY.md 索引失败")

                result.written = len(new_memories)
                self.logger.info("Claude: 写入 {} 条记忆到 {}".format(len(new_memories), mem_dir))

                # 检测是否有 pending 文件（主文件被锁时内容暂存于此）
                for check_path in (shared_file, index_path):
                    if check_path and _pending_path(check_path).exists():
                        result.pending += 1
                        result.errors.append(
                            "⚠ {} 主文件被锁定，内容已暂存到 {}".format(
                                check_path.name, _pending_path(check_path)
                            )
                        )

            except (OSError, UnicodeEncodeError) as e:
                err_str = str(e)
                if "Permission denied" in err_str or "Lock" in err_str or "被锁" in err_str:
                    result.errors.append("⚠ {} (跳过): {}".format(mem_dir, e))
                else:
                    result.errors.append("写入 {} 失败: {}".format(mem_dir, e))
                self.logger.error("Claude 写入失败: {}".format(e))

        # 标记已写入
        for mem in new_memories:
            self.sync_state.mark_written(agent_id, mem.content)

        return result

    def _find_memory_dirs(self, target_path: Path) -> list:
        """查找所有 Claude 项目的 memory 目录"""
        dirs = []
        path_str = str(target_path).lower()

        if "projects" in path_str:
            # target_path 已经是 projects 目录或其子目录
            if target_path.name == "memory":
                dirs.append(target_path)
            else:
                for d in target_path.glob("*/memory"):
                    if d.is_dir():
                        dirs.append(d)
        else:
            # target_path 是 .claude 目录
            projects_dir = target_path / "projects"
            if projects_dir.exists():
                for d in projects_dir.glob("*/memory"):
                    if d.is_dir():
                        dirs.append(d)

        return dirs

    # Claude 自愈钩子 v2：返回 dict（含 legacy + file_present）
    def extract_target_info(self, agent_id: str, target_path: Path) -> dict:
        memory_dirs = self._find_memory_dirs(target_path)
        hashes = set()
        legacy_count = 0
        file_present = False
        for d in memory_dirs:
            f = d / "shared_from_agents.md"
            if not f.exists():
                continue
            file_present = True
            try:
                text = _safe_read_text(f, default="")
            except Exception:
                continue
            if not text:
                continue
            import re
            for m in re.finditer(r"\[sync:([^\]]+)\]", text):
                raw = m.group(1)
                if "h:" in raw:
                    seg = raw.split("|")
                    part = dict(
                        (k.strip(), v.strip())
                        for k, v in (s.split(":", 1) for s in seg if ":" in s)
                    )
                    if "h" in part:
                        hashes.add(part["h"])
                else:
                    legacy_count += 1
        if legacy_count:
            self.logger.info(
                "Claude: 检测到 {} 条 legacy，保守保留 state".format(legacy_count))
        return {"hashes": hashes, "legacy": legacy_count, "file_present": file_present}

    def extract_hashes_in_target(self, agent_id: str, target_path: Path) -> set:
        return self.extract_target_info(agent_id, target_path)["hashes"]

    def _format_memories(self, memories: list[MemoryEntry]) -> str:
        """格式化记忆为 Claude 子文件格式

        每条记忆都嵌入显式同步 marker：
            [sync:<id>|h:<content_hash>|src:<agent_id>]
        这样 Claude 的目标子文件 (shared_from_agents.md) 即使被人类
        改了格式，reconciler 仍能从文本中精确解析 hash set。
        """
        lines = [
            "---",
            "name: 来自其他 Agent 的共享记忆",
            "description: 由记忆同步工具自动写入，包含其他 Agent 的知识",
            "type: shared",
            "sync_time: {}".format(datetime.now(timezone.utc).isoformat()),
            "---",
            "",
            "# 共享记忆",
            "",
            "以下记忆由同步工具从其他 Agent 自动导入。",
            "",
        ]

        for mem in memories:
            marker = format_sync_marker(mem)
            lines.append("## [{}] {}".format(mem.agent_id, mem.id))
            lines.append("")
            lines.append("- sync_marker: {}".format(marker))
            lines.append("- 来源: {}".format(mem.agent_id))
            lines.append("- 时间: {}".format(mem.timestamp))
            if mem.tags:
                lines.append("- 标签: {}".format(", ".join(mem.tags)))
            lines.append("- 信心: {}".format(mem.confidence))
            lines.append("")
            lines.append(strip_sync_markers(mem.content))
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Trae Writer
# ---------------------------------------------------------------------------

class TraeMemoryWriter(BaseMemoryWriter):
    """
    Trae 记忆写回器

    Trae 的 user_profile.md 是纯 Markdown，无 front matter。
    写回策略：追加到 ## Shared Knowledge 段。
    """

    SECTION_HEADER = "## Shared Knowledge"

    def write(
        self,
        agent_id: str,
        target_path: Path,
        memories: list[MemoryEntry],
        backup_dir: Path = None
    ) -> WriteBackResult:
        result = WriteBackResult(
            agent_id=agent_id,
            target_path=str(target_path),
            written=0,
            skipped=0,
            errors=[]
        )

        # target_path 可能是 .trae-cn/memory 或 .trae-cn
        if target_path.name == "memory":
            mem_dir = target_path
        else:
            mem_dir = target_path / "memory"

        profile_path = mem_dir / "user_profile.md"
        if not profile_path.exists():
            result.errors.append("Trae user_profile.md 不存在: {}".format(profile_path))
            return result

        # 过滤去重
        new_memories = []
        for mem in memories:
            if not self.sync_state.is_duplicate(agent_id, mem.content):
                new_memories.append(mem)
            else:
                result.skipped += 1

        if not new_memories:
            self.logger.info("Trae: 所有记忆已存在，跳过写入")
            return result

        try:
            # 备份
            if backup_dir:
                result.backup_path = self._do_backup(profile_path, backup_dir)

            content = _safe_read_text(profile_path, default="")
            if not content:
                # 读不到内容，说明文件可能被锁或不存在
                result.errors.append("⚠ Trae user_profile.md 读取失败: {} (跳过)".format(profile_path))
                self.logger.warning("Trae 读取失败: {}".format(profile_path))
                return result

            # ---- 通用污染检测+自愈 ----
            # 防止回声污染雪崩：若文件已膨胀到 5MB+ 或 marker 超千，
            # 自动备份原文件并重建干净骨架（保留 SECTION_HEADER 之前的本体）。
            pollution = self._detect_pollution(content)
            if pollution["polluted"]:
                self.logger.warning(
                    "Trae: 检测到文件污染 ({}), 触发自愈重建".format(pollution["reason"])
                )
                content = self._repair_polluted_file(
                    profile_path, content,
                    clean_prefix="# Trae User Profile\n\nThis file is managed by Trae. "
                                  "The ## Shared Knowledge section below is used by the "
                                  "multi-agent memory sync system.",
                    section_header=self.SECTION_HEADER,
                    sync_state=self.sync_state,
                    agent_id=agent_id,
                )

            # ---- 段头去重：只保留首个 ## Shared Knowledge ----
            # 防止历史污染遗留的多个段头导致解析混乱
            section_count = content.count(self.SECTION_HEADER)
            if section_count > 1:
                self.logger.warning(
                    "Trae: 检测到 {} 个重复段头, 截断保留首个".format(section_count)
                )
                # 保留首个 SECTION_HEADER 之前的本体 + 首个段头之后到第二个段头之前的内容
                parts = content.split(self.SECTION_HEADER, 2)
                prefix = parts[0].rstrip()
                first_section = parts[1] if len(parts) > 1 else ""
                content = prefix + "\n\n" + self.SECTION_HEADER + first_section
            elif self.SECTION_HEADER not in content:
                content = content.rstrip() + "\n\n" + self.SECTION_HEADER + "\n"

            # 追加记忆
            for mem in new_memories:
                marker = format_sync_marker(mem)
                clean_content = strip_sync_markers(mem.content)
                entry = "\n- {} {} — 来自 {} ({})\n".format(
                    marker, clean_content, mem.agent_id, mem.timestamp[:10]
                )
                content = content.rstrip() + entry

            # 写入（用 _safe_write_text 绕过文件锁）
            if not _safe_write_text(profile_path, content):
                result.errors.append("⚠ Trae user_profile.md 写入失败（含重试+原子替换）: {}".format(profile_path))
                self.logger.warning("Trae 写入失败: {}".format(profile_path))
                return result

            result.written = len(new_memories)
            self.logger.info("Trae: 写入 {} 条记忆到 {}".format(len(new_memories), profile_path))

            # 检测是否有 pending 文件
            if _pending_path(profile_path).exists():
                result.pending += 1
                result.errors.append(
                    "⚠ Trae user_profile.md 主文件被锁定，内容已暂存到 {}".format(
                        _pending_path(profile_path)
                    )
                )

        except (OSError, UnicodeEncodeError) as e:
            err_str = str(e)
            if "Permission denied" in err_str or "Lock" in err_str or "被锁" in err_str:
                result.errors.append("⚠ Trae 写入失败（已跳过）: {}".format(e))
            else:
                result.errors.append("Trae 写入失败: {}".format(e))
            self.logger.error("Trae 写入失败: {}".format(e))

        # 标记已写入
        for mem in new_memories:
            self.sync_state.mark_written(agent_id, mem.content)

        return result

    # ------------------------------------------------------------------
    # Trae 自愈钩子 v2：返回 dict（含 legacy + file_present）
    def extract_target_info(self, agent_id: str, target_path: Path) -> dict:
        if target_path.name == "memory":
            mem_dir = target_path
        else:
            mem_dir = target_path / "memory"
        profile_path = mem_dir / "user_profile.md"
        if not profile_path.exists():
            return {"hashes": set(), "legacy": 0, "file_present": False}
        try:
            text = _safe_read_text(profile_path, default="")
        except Exception:
            return {"hashes": set(), "legacy": 0, "file_present": True}
        hashes = set()
        legacy_count = 0
        if not text or self.SECTION_HEADER not in text:
            return {"hashes": hashes, "legacy": 0, "file_present": True}

        # ---- 污染检测：若文件已膨胀,触发自愈重建后返回空 ----
        # 避免在 52MB 污染文件里数出 1.7 万个 legacy marker 导致保守误判
        pollution = self._detect_pollution(text)
        if pollution["polluted"]:
            self.logger.warning(
                "Trae extract: 检测到文件污染 ({}), 触发自愈重建".format(pollution["reason"])
            )
            self._repair_polluted_file(
                profile_path, text,
                clean_prefix="# Trae User Profile\n\nThis file is managed by Trae. "
                              "The ## Shared Knowledge section below is used by the "
                              "multi-agent memory sync system.",
                section_header=self.SECTION_HEADER,
                sync_state=self.sync_state,
                agent_id=agent_id,
            )
            # 重建后文件无 sync marker,返回空集合让 reconciler 清理 orphan state
            return {"hashes": set(), "legacy": 0, "file_present": True}

        # ---- 只扫首个 SECTION_HEADER 段 ----
        # 防止多个重复段头导致 legacy 数量爆炸
        parts = text.split(self.SECTION_HEADER, 2)
        tail = parts[1] if len(parts) > 1 else ""
        for line in tail.splitlines():
            meta = parse_sync_marker(line)
            if not meta:
                continue
            h = meta.get("h")
            if h:
                hashes.add(h)
            elif "id" in meta:
                legacy_count += 1
        if legacy_count:
            self.logger.info(
                "Trae: 检测到 {} 条 legacy 同步条目，保守保留 state".format(legacy_count))
        return {"hashes": hashes, "legacy": legacy_count, "file_present": True}

    def extract_hashes_in_target(self, agent_id: str, target_path: Path) -> set:
        return self.extract_target_info(agent_id, target_path)["hashes"]


# ---------------------------------------------------------------------------
# Hermes Writer
# ---------------------------------------------------------------------------

class HermesMemoryWriter(BaseMemoryWriter):
    """
    Hermes 记忆写回器

    Hermes 的 MEMORY.md 用 § (U+00A7) 作为段落分隔符。
    写回策略：追加到文件末尾，用 § 分隔。
    需要尊重 .lock 锁文件。
    """

    def write(
        self,
        agent_id: str,
        target_path: Path,
        memories: list[MemoryEntry],
        backup_dir: Path = None
    ) -> WriteBackResult:
        result = WriteBackResult(
            agent_id=agent_id,
            target_path=str(target_path),
            written=0,
            skipped=0,
            errors=[]
        )

        # target_path 是 memories 目录
        md_path = target_path / "MEMORY.md"
        # 使用 .sync.lock 避免与 Agent 自身的 .lock 文件冲突
        lock_path = target_path / "MEMORY.md.sync.lock"

        if not md_path.exists():
            result.errors.append("Hermes MEMORY.md 不存在: {}".format(md_path))
            return result

        # 检查锁（超过 60 秒视为过期锁，自动清理；被外部锁定的锁文件也直接清理）
        if lock_path.exists():
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > 60:
                    self.logger.warning("清理过期锁文件 ({}秒): {}".format(int(age), lock_path))
                    try:
                        lock_path.unlink()
                    except OSError as e:
                        # 被外部锁定的锁文件无法 unlink，忽略
                        self.logger.warning("无法清理锁文件，继续: {}".format(e))
                else:
                    self.logger.warning("同步锁文件存在 ({}秒): {} - 尝试清理".format(int(age), lock_path))
                    # 60 秒内的新锁也尝试清理（OneDrive 锁可能让 stat 返回 0，直接清）
                    try:
                        lock_path.unlink()
                    except OSError as e:
                        # 清理失败，记录 warning 但继续执行
                        self.logger.warning("无法清理锁文件，继续: {}".format(e))
            except OSError as e:
                # stat 失败也假定为旧锁
                self.logger.warning("锁文件 stat 失败: {}，尝试清理".format(e))
                try:
                    lock_path.unlink()
                except OSError:
                    pass

        # 过滤去重
        new_memories = []
        for mem in memories:
            if not self.sync_state.is_duplicate(agent_id, mem.content):
                new_memories.append(mem)
            else:
                result.skipped += 1

        if not new_memories:
            self.logger.info("Hermes: 所有记忆已存在，跳过写入")
            return result

        try:
            # 创建锁（外部锁可能让 touch 失败，但不影响写回）
            try:
                lock_path.touch()
            except OSError as e:
                self.logger.warning("创建锁文件失败，继续: {}".format(e))

            # 备份
            if backup_dir:
                result.backup_path = self._do_backup(md_path, backup_dir)

            # 读取（用 _safe_read_text 绕过文件锁）
            content = _safe_read_text(md_path, default="")
            if not content and md_path.exists():
                # 读不到内容
                result.errors.append("⚠ Hermes MEMORY.md 读取失败（含重试+绕过）: {}".format(md_path))
                self.logger.warning("Hermes 读取失败: {}".format(md_path))
                return result

            # ---- 通用污染检测+自愈 ----
            pollution = self._detect_pollution(content)
            if pollution["polluted"]:
                self.logger.warning(
                    "Hermes: 检测到文件污染 ({}), 触发自愈重建".format(pollution["reason"])
                )
                content = self._repair_polluted_file(
                    md_path, content,
                    sync_state=self.sync_state,
                    agent_id=agent_id,
                )

            # 追加记忆（用 § 分隔）
            for mem in new_memories:
                marker = format_sync_marker(mem)
                clean_content = strip_sync_markers(mem.content)
                entry = "\n§\n{} — 来自 {} ({})\n".format(
                    marker, clean_content, mem.agent_id, mem.timestamp[:10]
                )
                content = content.rstrip() + entry

            # 写入（用 _safe_write_text 绕过文件锁）
            if not _safe_write_text(md_path, content):
                result.errors.append("⚠ Hermes MEMORY.md 写入失败（含重试+原子替换）: {}".format(md_path))
                self.logger.warning("Hermes 写入失败: {}".format(md_path))
                return result

            result.written = len(new_memories)
            self.logger.info("Hermes: 写入 {} 条记忆到 {}".format(len(new_memories), md_path))

            # 检测是否有 pending 文件
            if _pending_path(md_path).exists():
                result.pending += 1
                result.errors.append(
                    "⚠ Hermes MEMORY.md 主文件被锁定，内容已暂存到 {}".format(
                        _pending_path(md_path)
                    )
                )

        except (OSError, UnicodeEncodeError) as e:
            err_str = str(e)
            if "Permission denied" in err_str or "Lock" in err_str or "被锁" in err_str:
                result.errors.append("⚠ Hermes 写入失败（已跳过）: {}".format(e))
            else:
                result.errors.append("Hermes 写入失败: {}".format(e))
            self.logger.error("Hermes 写入失败: {}".format(e))

        finally:
            # 释放锁
            if lock_path.exists():
                try:
                    lock_path.unlink()
                except OSError:
                    pass

        # 标记已写入
        for mem in new_memories:
            self.sync_state.mark_written(agent_id, mem.content)

        return result

    # Hermes 自愈钩子 v2：直接返回 dict（含 legacy + file_present 信号）
    def extract_target_info(self, agent_id: str, target_path: Path) -> dict:
        md_path = target_path / "MEMORY.md"
        if not md_path.exists():
            return {"hashes": set(), "legacy": 0, "file_present": False}
        try:
            text = _safe_read_text(md_path, default="")
        except Exception:
            return {"hashes": set(), "legacy": 0, "file_present": True}
        if not text:
            return {"hashes": set(), "legacy": 0, "file_present": True}
        hashes = set()
        legacy_count = 0
        for seg in text.split("§"):
            meta = parse_sync_marker(seg)
            if not meta:
                continue
            h = meta.get("h")
            if h:
                hashes.add(h)
            elif "id" in meta:
                legacy_count += 1
        if legacy_count:
            self.logger.info(
                "Hermes: 检测到 {} 条 legacy 同步条目，保守保留 state".format(legacy_count))
        return {"hashes": hashes, "legacy": legacy_count, "file_present": True}

    def extract_hashes_in_target(self, agent_id: str, target_path: Path) -> set:
        return self.extract_target_info(agent_id, target_path)["hashes"]


# ---------------------------------------------------------------------------
# 通用 Writer（用于未知 Agent）
# ---------------------------------------------------------------------------

class GenericMarkdownWriter(BaseMemoryWriter):
    """
    通用 Markdown 记忆写回器

    适用于任何使用 Markdown 记忆文件的 Agent。
    写回策略：追加到 MEMORY.md / memory.md / memories.md 末尾。
    如果文件不存在，自动创建。
    """

    MEMORY_FILENAMES = ["MEMORY.md", "memory.md", "memories.md"]

    def write(
        self,
        agent_id: str,
        target_path: Path,
        memories: list[MemoryEntry],
        backup_dir: Path = None
    ) -> WriteBackResult:
        result = WriteBackResult(
            agent_id=agent_id,
            target_path=str(target_path),
            written=0,
            skipped=0,
            errors=[]
        )

        # 找到记忆文件
        md_path = self._find_memory_file(target_path)
        if md_path is None:
            md_path = target_path / "MEMORY.md"
            try:
                target_path.mkdir(parents=True, exist_ok=True)
                md_path.write_text("# Memory\n", encoding="utf-8")
                self.logger.info("通用 Writer: 创建 {}".format(md_path))
            except OSError as e:
                result.errors.append("创建记忆文件失败: {}".format(e))
                return result

        # 使用 .sync.lock 避免与 Agent 自身的 .lock 文件冲突
        lock_path = md_path.with_suffix(md_path.suffix + ".sync.lock")
        if lock_path.exists():
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > 60:
                    self.logger.warning("清理过期锁文件 ({}秒): {}".format(int(age), lock_path))
                    try:
                        lock_path.unlink()
                    except OSError as e:
                        self.logger.warning("无法清理锁文件，继续: {}".format(e))
                else:
                    # 新锁也尝试清理（外部锁可能让 stat 返回 0）
                    self.logger.warning("同步锁文件存在 ({}秒): {} - 尝试清理".format(int(age), lock_path))
                    try:
                        lock_path.unlink()
                    except OSError as e:
                        self.logger.warning("无法清理锁文件，继续: {}".format(e))
            except OSError as e:
                # stat 失败也假定为旧锁
                self.logger.warning("锁文件 stat 失败: {}，尝试清理".format(e))
                try:
                    lock_path.unlink()
                except OSError:
                    pass

        new_memories = [m for m in memories if not self.sync_state.is_duplicate(agent_id, m.content)]
        result.skipped = len(memories) - len(new_memories)

        if not new_memories:
            self.logger.info("通用 Writer ({}): 所有记忆已存在".format(agent_id))
            return result

        try:
            try:
                lock_path.touch()
            except OSError as e:
                self.logger.warning("创建锁文件失败，继续: {}".format(e))

            if backup_dir:
                result.backup_path = self._do_backup(md_path, backup_dir)

            # 用 _safe_read_text 读取（绕过文件锁）
            content = _safe_read_text(md_path, default="")

            # ---- 通用污染检测+自愈 ----
            # 防止回声污染雪崩（codepilot MEMORY.md 曾膨胀到 52MB / 3万 marker）
            pollution = self._detect_pollution(content)
            if pollution["polluted"]:
                self.logger.warning(
                    "通用 Writer ({}): 检测到文件污染 ({}), 触发自愈重建".format(
                        agent_id, pollution["reason"])
                )
                content = self._repair_polluted_file(
                    md_path, content,
                    clean_prefix="# Memory\n",
                    sync_state=self.sync_state,
                    agent_id=agent_id,
                )

            for mem in new_memories:
                marker = format_sync_marker(mem)
                clean_content = strip_sync_markers(mem.content)
                # 用内容第一行作为标题摘要，避免重复写入完整内容
                first_line = clean_content.split("\n", 1)[0][:80]
                entry = "\n---\n{} {} — 来自 {} ({})\n\n{}\n".format(
                    marker, first_line, mem.agent_id, mem.timestamp[:10], clean_content
                )
                content = content.rstrip() + entry

            # 用 _safe_write_text 写入（绕过文件锁）
            if not _safe_write_text(md_path, content):
                result.errors.append("⚠ 通用 Writer 写入失败（含重试+原子替换）: {}".format(md_path))
                self.logger.warning("通用 Writer 写入失败: {}".format(md_path))
                return result

            result.written = len(new_memories)
            self.logger.info("通用 Writer ({}): 写入 {} 条记忆".format(agent_id, len(new_memories)))

            # 检测是否有 pending 文件
            if _pending_path(md_path).exists():
                result.pending += 1
                result.errors.append(
                    "⚠ {} 主文件被锁定，内容已暂存到 {}".format(
                        md_path.name, _pending_path(md_path)
                    )
                )

        except (OSError, UnicodeEncodeError) as e:
            err_str = str(e)
            if "Permission denied" in err_str or "Lock" in err_str or "被锁" in err_str:
                result.errors.append("⚠ 通用 Writer 写入失败（已跳过）: {}".format(e))
            else:
                # 诊断：记录详细的权限信息
                import os, sys
                diag_info = "errno={}, winerror={}, uid={}, gid={}, cwd={}".format(
                    getattr(e, 'errno', '?'),
                    getattr(e, 'winerror', '?'),
                    getattr(os, 'getuid', lambda: '?')(),
                    getattr(os, 'getgid', lambda: '?')(),
                    getattr(os, 'getcwd', lambda: '?')(),
                )
                result.errors.append("通用 Writer 写入失败: {} [{}]".format(e, diag_info))
            self.logger.error("通用 Writer 写入失败: {}".format(e))
        finally:
            if lock_path.exists():
                try:
                    lock_path.unlink()
                except OSError:
                    pass

        for mem in new_memories:
            self.sync_state.mark_written(agent_id, mem.content)

        return result

    # Generic 自愈钩子 v2：返回 dict（含 legacy + file_present）
    def extract_target_info(self, agent_id: str, target_path: Path) -> dict:
        candidates = []
        if target_path.is_file():
            candidates.append(target_path)
        elif target_path.is_dir():
            names = list(self.MEMORY_FILENAMES) + [
                "shared.md", "memory_shared.md", "shared_from_agents.md",
            ]
            for name in names:
                p = target_path / name
                if p.exists() and p.is_file():
                    candidates.append(p)

        hashes = set()
        legacy_count = 0
        file_present = bool(candidates)
        for fp in candidates:
            try:
                text = _safe_read_text(fp, default="")
            except Exception:
                continue
            if not text:
                continue
            for seg in text.split("\n---\n"):
                meta = parse_sync_marker(seg)
                if not meta:
                    continue
                h = meta.get("h")
                if h:
                    hashes.add(h)
                elif "id" in meta:
                    legacy_count += 1
        if legacy_count:
            self.logger.info(
                "Generic({}): 检测到 {} 条 legacy，保守保留 state".format(
                    agent_id, legacy_count))
        return {"hashes": hashes, "legacy": legacy_count, "file_present": file_present}

    def extract_hashes_in_target(self, agent_id: str, target_path: Path) -> set:
        return self.extract_target_info(agent_id, target_path)["hashes"]

    def _find_memory_file(self, target_path: Path) -> Optional[Path]:
        """查找现有的记忆文件"""
        for name in self.MEMORY_FILENAMES:
            p = target_path / name
            if p.exists():
                return p
        return None


# ---------------------------------------------------------------------------
# Writer 工厂
# ---------------------------------------------------------------------------

WRITER_REGISTRY = {
    "claude": ClaudeMemoryWriter,
    "claude-appdata": ClaudeMemoryWriter,
    "trae": TraeMemoryWriter,
    "trae-appdata": TraeMemoryWriter,
    "hermes": HermesMemoryWriter,
    "hermes-appdata": HermesMemoryWriter,
    "codebuddy": GenericMarkdownWriter,
    "codepilot": GenericMarkdownWriter,
    "openclaw": GenericMarkdownWriter,
    "pi-web": GenericMarkdownWriter,   # v1.3.7: pi-web 支持
    "pi": GenericMarkdownWriter,
    "clawdbot": GenericMarkdownWriter,
}

# v1.3.7: 凡是 generic- 开头的未知 agent，自动使用 GenericMarkdownWriter
# 已在 get_writer() 中通过 fallback 实现


def get_writer(agent_id: str, sync_state: SyncState = None) -> BaseMemoryWriter:
    """
    根据 agent_id 获取对应的写回器。
    已知 agent 使用专用 writer，未知 agent 使用通用 Markdown writer。

    Parameters
    ----------
    agent_id : str
        Agent ID
    sync_state : SyncState, optional
        去重状态管理器

    Returns
    -------
    BaseMemoryWriter
        写回器实例
    """
    writer_cls = WRITER_REGISTRY.get(agent_id)
    if writer_cls is None:
        # 尝试模糊匹配
        for key, cls in WRITER_REGISTRY.items():
            if key in agent_id or agent_id in key:
                writer_cls = cls
                break
    if writer_cls is None:
        # 使用通用 writer，不再抛异常
        writer_cls = GenericMarkdownWriter
    return writer_cls(sync_state=sync_state)


def rollback_last_sync(backup_dir: Path, target_files: dict) -> int:
    """
    回滚上次同步

    Parameters
    ----------
    backup_dir : Path
        备份目录（.sync_backups/<timestamp>/）
    target_files : dict
        {备份文件名: 目标文件路径}

    Returns
    -------
    int
        成功回滚的文件数
    """
    logger = get_logger()
    restored = 0

    if not backup_dir.exists():
        logger.warning("备份目录不存在: {}".format(backup_dir))
        return 0

    for bak_name, target_path in target_files.items():
        bak_file = backup_dir / bak_name
        if bak_file.exists():
            try:
                shutil.copy2(str(bak_file), str(target_path))
                logger.info("回滚: {} → {}".format(bak_file, target_path))
                restored += 1
            except OSError as e:
                logger.error("回滚失败 {}: {}".format(bak_name, e))
        else:
            logger.warning("备份文件不存在: {}".format(bak_file))

    return restored
