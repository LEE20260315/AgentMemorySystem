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
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent_memory import (
    MemoryEntry, content_hash, get_logger, get_config,
)


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


# ---------------------------------------------------------------------------
# 去重状态管理
# ---------------------------------------------------------------------------

class SyncState:
    """管理同步去重状态（.sync_state.json）"""

    def __init__(self, state_path: Path = None):
        if state_path is None:
            state_path = Path.home() / ".agent_memory" / ".sync_state.json"
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
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

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

                # 写入 shared_from_agents.md
                shared_file.write_text(content, encoding="utf-8")

                # 更新 MEMORY.md 索引
                index_path = mem_dir / "MEMORY.md"
                if index_path.exists():
                    index_content = index_path.read_text(encoding="utf-8")
                    link = "- [来自其他 Agent 的共享记忆](shared_from_agents.md) — 自动同步"
                    if "shared_from_agents" not in index_content:
                        index_content = index_content.rstrip() + "\n\n" + link + "\n"
                        index_path.write_text(index_content, encoding="utf-8")

                result.written = len(new_memories)
                self.logger.info("Claude: 写入 {} 条记忆到 {}".format(len(new_memories), mem_dir))

            except (OSError, UnicodeEncodeError) as e:
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

            content = profile_path.read_text(encoding="utf-8")

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

            profile_path.write_text(content, encoding="utf-8")
            result.written = len(new_memories)
            self.logger.info("Trae: 写入 {} 条记忆到 {}".format(len(new_memories), profile_path))

        except (OSError, UnicodeEncodeError) as e:
            result.errors.append("写入失败: {}".format(e))
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
        lock_path = target_path / "MEMORY.md.lock"

        if not md_path.exists():
            result.errors.append("Hermes MEMORY.md 不存在: {}".format(md_path))
            return result

        # 检查锁
        if lock_path.exists():
            result.errors.append("Hermes 记忆文件被锁定 ({}), 请先关闭 Hermes".format(lock_path))
            self.logger.warning("Hermes 锁文件存在: {}".format(lock_path))
            return result

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
            # 创建锁
            lock_path.touch()

            # 备份
            if backup_dir:
                result.backup_path = self._do_backup(md_path, backup_dir)

            content = md_path.read_text(encoding="utf-8")

            # 追加记忆（用 § 分隔）
            for mem in new_memories:
                marker = "[sync:{}]".format(mem.id)
                entry = "\n§\n{} {} — 来自 {} ({})\n".format(
                    marker, mem.content, mem.agent_id, mem.timestamp[:10]
                )
                content = content.rstrip() + entry

            md_path.write_text(content, encoding="utf-8")
            result.written = len(new_memories)
            self.logger.info("Hermes: 写入 {} 条记忆到 {}".format(len(new_memories), md_path))

        except (OSError, UnicodeEncodeError) as e:
            result.errors.append("写入失败: {}".format(e))
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
# Writer 工厂
# ---------------------------------------------------------------------------

WRITER_REGISTRY = {
    "claude": ClaudeMemoryWriter,
    "claude-appdata": ClaudeMemoryWriter,
    "trae": TraeMemoryWriter,
    "trae-appdata": TraeMemoryWriter,
    "hermes": HermesMemoryWriter,
    "hermes-appdata": HermesMemoryWriter,
}


def get_writer(agent_id: str, sync_state: SyncState = None) -> BaseMemoryWriter:
    """
    根据 agent_id 获取对应的写回器

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
        raise ValueError("未知的 Agent ID: {}, 无法确定写回格式".format(agent_id))
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
    import shutil
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
