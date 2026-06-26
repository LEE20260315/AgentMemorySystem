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

    def _do_backup(self, file_path: Path, backup_dir: Path) -> Optional[str]:
        if backup_dir and file_path.exists():
            bp = backup_file(file_path, backup_dir)
            if bp:
                self.logger.info("备份: {} → {}".format(file_path, bp))
                return str(bp)
        return None


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

    def _format_memories(self, memories: list[MemoryEntry]) -> str:
        """格式化记忆为 Claude 子文件格式"""
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
            lines.append("## [{}] {}".format(mem.agent_id, mem.id))
            lines.append("")
            lines.append("- 来源: {}".format(mem.agent_id))
            lines.append("- 时间: {}".format(mem.timestamp))
            if mem.tags:
                lines.append("- 标签: {}".format(", ".join(mem.tags)))
            lines.append("- 信心: {}".format(mem.confidence))
            lines.append("")
            lines.append(mem.content)
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

            # 确保有 Shared Knowledge section
            if self.SECTION_HEADER not in content:
                content = content.rstrip() + "\n\n" + self.SECTION_HEADER + "\n"

            # 追加记忆
            for mem in new_memories:
                marker = "[sync:{}]".format(mem.id)
                entry = "\n- {} {} — 来自 {} ({})\n".format(
                    marker, mem.content, mem.agent_id, mem.timestamp[:10]
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

            # 追加记忆（用 § 分隔）
            for mem in new_memories:
                marker = "[sync:{}]".format(mem.id)
                entry = "\n§\n{} {} — 来自 {} ({})\n".format(
                    marker, mem.content, mem.agent_id, mem.timestamp[:10]
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

            for mem in new_memories:
                marker = "[sync:{}]".format(mem.id)
                # 用内容第一行作为标题摘要，避免重复写入完整内容
                first_line = mem.content.split("\n", 1)[0][:80]
                entry = "\n---\n{} {} — 来自 {} ({})\n\n{}\n".format(
                    marker, first_line, mem.agent_id, mem.timestamp[:10], mem.content
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
}


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
