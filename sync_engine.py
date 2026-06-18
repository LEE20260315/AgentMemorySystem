"""
记忆同步引擎
===========
核心同步流程：发现 Agent → 提取记忆 → 融合 → 写回各 Agent

复用 agent_memory.py 中的现有功能：
- detect_agents() - 鲁棒性路径探测
- extract_local_to_fused() - 记忆提取
- MemoryMerger.full_sync() - 跨 Agent 融合
- check_onedrive_conflicts() - OneDrive 冲突检测
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from agent_memory import (
    AgentRegistry, ConfigManager, MemoryDatabase, MemoryEntry,
    MemoryMerger, check_onedrive_conflicts, create_merger,
    detect_agents, extract_local_to_fused, get_config, get_logger,
    get_loaded_context, load_private_memories, startup,
)
from sync_writers import (
    SyncState, WriteBackResult, backup_file, get_writer,
    rollback_last_sync,
)


# ---------------------------------------------------------------------------
# 同步报告
# ---------------------------------------------------------------------------

@dataclass
class SyncReport:
    """同步运行报告"""
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0.0
    device: str = ""

    # 发现阶段
    agents_detected: dict = field(default_factory=dict)
    conflicts_found: list = field(default_factory=list)

    # 提取阶段
    extract_results: dict = field(default_factory=dict)

    # 融合阶段
    merge_results: dict = field(default_factory=dict)

    # 写回阶段
    writeback_results: dict = field(default_factory=dict)

    # 汇总
    total_extracted: int = 0
    total_merged: int = 0
    total_written: int = 0
    total_skipped: int = 0
    total_pending: int = 0  # 因主文件锁定而暂存到 .pending 的文件数
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)  # 新增：软警告（Permission denied 等可恢复错误）

    def summary_text(self) -> str:
        """生成人类可读的汇总文本"""
        lines = [
            "=== 同步报告 ===",
            "时间: {} → {}".format(self.start_time, self.end_time),
            "耗时: {:.1f} 秒".format(self.duration_seconds),
            "设备: {}".format(self.device),
            "",
            "发现 Agent: {}".format(
                ", ".join(self.agents_detected.keys()) if self.agents_detected else "无"
            ),
            "OneDrive 冲突: {} 个".format(len(self.conflicts_found)),
            "",
            "提取: {} 条".format(self.total_extracted),
            "融合: {} 条新增共享".format(self.total_merged),
            "写回: {} 条".format(self.total_written),
            "跳过(去重): {} 条".format(self.total_skipped),
            "待合并: {} 个文件".format(self.total_pending),
        ]

        if self.warnings:
            lines.append("")
            lines.append("⚠ 警告（文件锁定等可恢复错误，不影响同步）:")
            for warn in self.warnings:
                lines.append("  - {}".format(warn))

        if self.errors:
            lines.append("")
            lines.append("✗ 错误:")
            for err in self.errors:
                lines.append("  - {}".format(err))

        # 各 Agent 详情
        for agent_id, wb in self.writeback_results.items():
            lines.append("")
            lines.append("{}:".format(agent_id))
            lines.append("  写入: {} 条".format(wb.written))
            lines.append("  跳过: {} 条".format(wb.skipped))
            lines.append("  待合并: {} 个".format(wb.pending))
            lines.append("  目标: {}".format(wb.target_path))
            if wb.errors:
                for err in wb.errors:
                    err_str = str(err)
                    if "Permission denied" in err_str or "⚠" in err_str or "跳过" in err_str:
                        lines.append("  ⚠ {}".format(err))
                    else:
                        lines.append("  错误: {}".format(err))

        # 总结：判断整体成功
        if self.errors:
            lines.append("")
            lines.append("结果: ⚠ 部分错误（请检查上述错误）")
        elif self.warnings and self.total_written == 0:
            lines.append("")
            lines.append("结果: ⚠ 写回被跳过（目标文件被锁定，已暂存 .pending）")
        elif self.warnings:
            lines.append("")
            lines.append("结果: ✓ 成功（{} 个文件被锁定跳过）".format(len([w for w in self.warnings if "Permission" in str(w) or "⚠" in str(w)])))
        elif self.total_extracted > 0 or self.total_merged > 0 or self.total_written > 0 or self.total_pending > 0:
            lines.append("")
            lines.append("结果: ✓ 全部成功")
        else:
            lines.append("")
            lines.append("结果: 无操作（无新记忆需要同步）")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 同步引擎
# ---------------------------------------------------------------------------

class SyncEngine:
    """
    记忆同步引擎

    执行完整的同步流程：
    ① detect_agents()         → 发现本地 agent
    ② check_onedrive_conflicts() → 扫描冲突文件
    ③ for each agent:
         scan_local()          → 读取本地记忆
         extract_local_to_fused() → 写入 OneDrive 融合层
    ④ full_merge()             → 跨 agent 融合
    ⑤ for each agent:
         writer.write()        → 按格式写回本地（带去重）
    ⑥ generate_report()        → 生成汇总
    """

    def __init__(
        self,
        config: ConfigManager = None,
        on_progress: Callable[[str], None] = None,
    ):
        """
        初始化同步引擎

        Parameters
        ----------
        config : ConfigManager, optional
            配置管理器
        on_progress : Callable[[str], None], optional
            进度回调函数，接收日志消息字符串
        """
        self.config = config or get_config()
        self.logger = get_logger()
        self.on_progress = on_progress or (lambda msg: None)
        self.sync_state = SyncState()

        # 确定 OneDrive 融合层根目录
        memory_root = self.config.get("paths.memory_root", None)
        if memory_root and memory_root != "auto":
            self.root = Path(memory_root)
        else:
            self.root = Path(__file__).parent / "data"
        self.root.mkdir(parents=True, exist_ok=True)

        # 备份目录
        self.backup_dir = self.root / ".sync_backups" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def _emit(self, msg: str):
        """发送进度消息"""
        self.logger.info(msg)
        self.on_progress(msg)

    def run(self) -> SyncReport:
        """
        执行完整同步流程

        Returns
        -------
        SyncReport
            同步报告
        """
        # 设备名：优先用配置，否则用 hostname，再否则用 "unknown"
        import socket
        device_name = self.config.get("device_name", None)
        if not device_name:
            try:
                device_name = socket.gethostname().lower()
            except Exception:
                device_name = "unknown"

        report = SyncReport(
            start_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            device=device_name,
        )
        start_ts = time.time()

        try:
            # ① 发现 Agent
            self._emit("正在检测本地 Agent...")
            detected = detect_agents(self.config, force_redetect=False)
            report.agents_detected = detected

            if not detected:
                self._emit("未发现任何 Agent，请检查安装路径")
                report.errors.append("未发现任何 Agent")
                return report

            self._emit("发现 {} 个 Agent: {}".format(
                len(detected), ", ".join(detected.keys())
            ))

            # ② OneDrive 冲突检测
            conflicts = check_onedrive_conflicts(self.root)
            report.conflicts_found = [str(c) for c in conflicts]
            if conflicts:
                self._emit("警告: 发现 {} 个 OneDrive 冲突文件".format(len(conflicts)))
                for c in conflicts[:5]:
                    self._emit("  - {}".format(c))
                if len(conflicts) > 5:
                    self._emit("  ... 还有 {} 个".format(len(conflicts) - 5))

                conflict_action = self.config.get("sync_tool.conflict_action", "prompt")
                if conflict_action == "skip":
                    self._emit("配置为冲突时跳过，本次同步终止")
                    report.errors.append("OneDrive 冲突，同步跳过")
                    return report

            # ③ 提取各 Agent 记忆到融合层
            self._emit("融合层目录: {}".format(self.root))
            self._emit("开始提取各 Agent 记忆...")
            registry = AgentRegistry(root=self.root)

            for agent_id, agent_info in detected.items():
                # 去掉 -appdata 后缀用于融合层目录
                extract_id = agent_id.replace("-appdata", "")
                agent_path = Path(agent_info["path"])
                local_files = agent_info.get("memory_files", [])

                # 如果缓存中没有 memory_files，从路径扫描
                if not local_files:
                    from agent_memory import _scan_agent_memory_files
                    local_files = _scan_agent_memory_files(
                        agent_id, agent_path
                    )

                self._emit("提取 {} ({}): {} 个文件".format(
                    agent_id, agent_path, len(local_files)))

                ext_result = extract_local_to_fused(
                    agent_id=extract_id,
                    root=self.root,
                    local_files=local_files,
                    registry=registry,
                )
                report.extract_results[agent_id] = ext_result
                report.total_extracted += ext_result.get("extracted", 0)

                self._emit("  提取 {} 条, 跳过 {} 条".format(
                    ext_result.get("extracted", 0),
                    ext_result.get("skipped", 0)
                ))

            # ④ 跨 Agent 融合
            self._emit("开始跨 Agent 融合...")
            self._emit("共享数据库: {}".format(self.root / "shared.db"))
            agent_dbs = {}
            for agent_id in detected:
                extract_id = agent_id.replace("-appdata", "")
                db_path = self.root / ("agent_" + extract_id) / "memories.db"
                if db_path.exists():
                    agent_dbs[extract_id] = db_path
                    self._emit("  Agent DB: {} -> {}".format(extract_id, db_path))

            if len(agent_dbs) >= 2:
                merger = create_merger(
                    shared_db_path=self.root / "shared.db",
                    agent_configs=agent_dbs,
                )
                merge_results = merger.full_sync()
                report.merge_results = merge_results

                # 统计融合新增
                for key, val in merge_results.items():
                    synced = val.get("synced", 0) if isinstance(val, dict) else 0
                    report.total_merged += synced

                self._emit("融合完成")
            else:
                self._emit("只有 {} 个 Agent 有数据库，跳过融合".format(len(agent_dbs)))

            # ⑤ 写回各 Agent
            self._emit("开始写回各 Agent...")
            self._emit("写回目标: {}".format(
                ", ".join("{}={}".format(aid, info["path"]) for aid, info in detected.items())
            ))

            for agent_id, agent_info in detected.items():
                extract_id = agent_id.replace("-appdata", "")
                target_path = Path(agent_info["path"])

                # 从融合层读取共享记忆
                shared_memories = self._load_shared_memories(extract_id)
                if not shared_memories:
                    self._emit("{}: 无共享记忆需要写回".format(agent_id))
                    continue

                self._emit("写回 {}: {} 条共享记忆".format(agent_id, len(shared_memories)))

                writer = get_writer(agent_id, self.sync_state)
                # 临时禁用备份，避免备份到受监控目录触发 Permission denied
                wb_result = writer.write(
                    agent_id=extract_id,
                    target_path=target_path,
                    memories=shared_memories,
                    backup_dir=None,
                )
                report.writeback_results[agent_id] = wb_result
                report.total_written += wb_result.written
                report.total_skipped += wb_result.skipped
                report.total_pending += wb_result.pending

                if wb_result.errors:
                    for err in wb_result.errors:
                        err_str = str(err)
                        if "Permission denied" in err_str or "跳过" in err_str or "⚠" in err_str:
                            # 文件被其他进程锁定/跳过，属于预期情况，记录为 warning
                            report.warnings.append(err_str)
                            self._emit("  ⚠ {}".format(err_str))
                        else:
                            report.errors.append(err_str)

                self._emit("  写入 {} 条, 跳过 {} 条, 待合并 {} 个".format(
                    wb_result.written, wb_result.skipped, wb_result.pending
                ))

            # ⑥ 保存去重状态
            try:
                self.sync_state.save()
            except PermissionError:
                self._emit("⚠ 去重状态保存失败（文件被锁定），下次同步可能重复")

        except PermissionError as e:
            self._emit("⚠ 文件被锁定: {}".format(e))
        except Exception as e:
            self.logger.error("同步异常: {}".format(e), exc_info=True)
            report.errors.append("同步异常: {}".format(e))

        finally:
            end_ts = time.time()
            report.end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            report.duration_seconds = end_ts - start_ts

        self._emit("同步完成, 耗时 {:.1f} 秒".format(report.duration_seconds))
        return report

    def _load_shared_memories(self, agent_id: str) -> list:
        """从融合层读取指定 Agent 的共享记忆"""
        shared_db = self.root / "shared.db"
        if not shared_db.exists():
            return []

        memories = []
        try:
            with MemoryDatabase(shared_db) as db:
                # 读取所有共享记忆，排除来自自身的
                cursor = db.conn.execute(
                    "SELECT * FROM memories WHERE agent_id != ? ORDER BY timestamp DESC LIMIT 500",
                    (agent_id,)
                )
                for row in cursor.fetchall():
                    entry = db._row_to_entry(row)
                    memories.append(entry)
        except Exception as e:
            self.logger.warning("读取共享记忆失败: {}".format(e))

        return memories

    def rollback(self) -> int:
        """
        回滚上次同步

        Returns
        -------
        int
            成功回滚的文件数
        """
        if not self.backup_dir.exists():
            # 找最近的备份目录
            backups_root = self.root / ".sync_backups"
            if not backups_root.exists():
                self._emit("没有找到任何备份")
                return 0

            backup_dirs = sorted(backups_root.iterdir(), reverse=True)
            if not backup_dirs:
                self._emit("备份目录为空")
                return 0

            self.backup_dir = backup_dirs[0]

        self._emit("回滚备份: {}".format(self.backup_dir))

        # 构建 {备份文件名: 目标路径} 映射
        target_files = {}
        for bak_file in self.backup_dir.glob("*.bak"):
            # 文件名格式: agent_id__filename.bak
            name = bak_file.stem  # 去掉 .bak
            # 尝试从写回结果中找到原始路径
            for agent_id, wb in self.report.writeback_results.items() if hasattr(self, 'report') else []:
                if name.replace("__", "_").startswith(agent_id):
                    target_files[bak_file.name] = Path(wb.target_path) / name.split("__", 1)[-1]

        # 简单回滚：直接复制所有 .bak 文件回去
        import shutil
        restored = 0
        for bak_file in self.backup_dir.glob("*.bak"):
            # 从文件名推断目标
            # 这里简化处理：用户可以手动指定
            try:
                # 读取备份日志
                log_file = self.backup_dir / "backup_log.json"
                if log_file.exists():
                    import json
                    with open(log_file, "r", encoding="utf-8") as f:
                        log_data = json.load(f)
                    for entry in log_data:
                        if entry.get("backup_name") == bak_file.name:
                            target = Path(entry["target_path"])
                            shutil.copy2(str(bak_file), str(target))
                            self._emit("回滚: {} → {}".format(bak_file.name, target))
                            restored += 1
                            break
            except Exception as e:
                self._emit("回滚 {} 失败: {}".format(bak_file.name, e))

        self._emit("回滚完成, 恢复 {} 个文件".format(restored))
        return restored


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def run_sync(on_progress: Callable[[str], None] = None) -> SyncReport:
    """
    执行一次完整同步（便捷入口）

    Parameters
    ----------
    on_progress : Callable[[str], None], optional
        进度回调

    Returns
    -------
    SyncReport
        同步报告
    """
    engine = SyncEngine(on_progress=on_progress)
    return engine.run()
