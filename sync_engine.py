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
    MemoryMerger, check_onedrive_conflicts, content_hash, create_merger,
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
    errors: list = field(default_factory=list)

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
        ]

        if self.errors:
            lines.append("")
            lines.append("错误:")
            for err in self.errors:
                lines.append("  - {}".format(err))

        # 各 Agent 详情
        for agent_id, wb in self.writeback_results.items():
            lines.append("")
            lines.append("{}:".format(agent_id))
            lines.append("  写入: {} 条".format(wb.written))
            lines.append("  跳过: {} 条".format(wb.skipped))
            lines.append("  目标: {}".format(wb.target_path))
            if wb.errors:
                for err in wb.errors:
                    lines.append("  错误: {}".format(err))

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
        dry_run: bool = False,
    ):
        """
        初始化同步引擎

        Parameters
        ----------
        config : ConfigManager, optional
            配置管理器
        on_progress : Callable[[str], None], optional
            进度回调函数，接收日志消息字符串
        dry_run : bool, optional
            试运行模式：只打印流程，不实际写回 Agent 文件
            (提取、融合仍执行以验证流程；写回阶段跳过)
        """
        self.config = config or get_config()
        self.logger = get_logger()
        self.on_progress = on_progress or (lambda msg: None)
        self.sync_state = SyncState()
        self.dry_run = dry_run

        # 确定 OneDrive 融合层根目录
        # v2.1.0: 统一数据根解析 —— 与 GUI/SyncState/detect_agents 一致走 get_data_root()
        # 修复历史分裂：引擎曾硬编码 <repo>/data，而 GUI/状态用 AgentMemory/（BAT 注入
        # AGENT_MEMORY_DATA_DIR），导致同步引擎与状态/日志各写各的目录。
        memory_root = self.config.get("paths.memory_root", None)
        if memory_root and memory_root != "auto":
            self.root = Path(memory_root)
        else:
            from safe_io import get_data_root
            self.root = get_data_root()
        self.root.mkdir(parents=True, exist_ok=True)

        # 备份目录
        self.backup_dir = self.root / ".sync_backups" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def _emit(self, msg: str):
        """发送进度消息"""
        self.logger.info(msg)
        self.on_progress(msg)

    def _resolve_device_name(self) -> str:
        """从 device_config.json 解析当前机器的设备名（用于同步报告）。

        优先级：
        1. data/device_config.json 的 devices 字典按 hostname 匹配
        2. data/device_config.json 的 source_device / default_device 字段
        3. config.get("device_name")
        4. socket.gethostname()
        """
        import json
        import socket

        dc_path = self.root / "device_config.json"
        if dc_path.exists():
            try:
                dc = json.loads(dc_path.read_text(encoding="utf-8"))
                devices = dc.get("devices", {})
                current_hostname = socket.gethostname().lower()
                current_home = str(Path.home())

                # hostname 匹配
                for name, info in devices.items():
                    if not isinstance(info, dict):
                        continue
                    if info.get("hostname", "").lower() == current_hostname:
                        return name

                # user_home 匹配
                for name, info in devices.items():
                    if not isinstance(info, dict):
                        continue
                    if info.get("user_home", "") and Path(info["user_home"]).resolve() == Path(current_home).resolve():
                        return name

                # default_device
                if dc.get("default_device"):
                    return dc["default_device"]
                if dc.get("source_device"):
                    return dc["source_device"]
            except Exception as e:
                self.logger.warning("读取 device_config.json 失败: {}".format(e))

        # 回退
        return self.config.get("device_name") or socket.gethostname()

    def run(self) -> SyncReport:
        """
        执行完整同步流程

        Returns
        -------
        SyncReport
            同步报告
        """
        report = SyncReport(
            start_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            device=self._resolve_device_name(),
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

            # ②.5 v2.0.1: 清理数据库中的污染条目（回声污染 / sync 产物）
            # 在提取前执行，避免污染条目再次被写入 memory_*.md
            if not self.dry_run:
                purge_result = self._purge_polluted_entries()
                if purge_result["purged"] > 0:
                    self._emit("🧹 清理污染条目: {} 条 (from {} DBs)".format(
                        purge_result["purged"], purge_result["dbs_scanned"]))
                    if purge_result["purged"] >= 10:
                        self._emit("  ⚠ 检测到大量污染条目，建议运行 'python tools/shrink_memory_files.py' 清理 .md 文件")

            # ②.6 v2.1.0: 修复 FTS 索引孤儿（历史膨胀），低频执行
            # 只在同步开始时检查一次，孤儿超过阈值才重建，避免每次同步都 VACUUM
            if not self.dry_run:
                fts_result = self._repair_fts_if_needed()
                if fts_result and fts_result.get("repaired", 0) > 0:
                    self._emit("🧹 修复 FTS 索引孤儿: {} 条 (回收 {:.2f}MB)".format(
                        fts_result["repaired"], fts_result.get("saved_mb", 0.0)))

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

                # ★ v2.0 修复：reconcile 移到 _load_shared_memories 之前
                # 先对齐 SyncState 与目标文件，再用干净的 state 过滤共享记忆
                # 否则记忆已被旧 state 过滤掉，reconcile 来不及救
                writer_for_state = get_writer(agent_id, self.sync_state)
                target_info = writer_for_state.extract_target_info(extract_id, target_path)
                reconcile_result = self.sync_state.reconcile_with_target_hashes(
                    extract_id,
                    target_info["hashes"],
                    target_info.get("legacy", 0),
                    target_info.get("file_present", True),
                )
                if reconcile_result.get("removed", 0) > 0:
                    self._emit("  reconcile {}: 清理 {} 条孤儿 hash, 保留 {} 条".format(
                        agent_id, reconcile_result["removed"], reconcile_result.get("kept", 0)))
                if reconcile_result.get("force_cleared"):
                    self._emit("  ⚠ {}: legacy marker 超阈值，已强制清除 SyncState".format(agent_id))

                # 从融合层读取共享记忆（force_refresh 跳过 known_hashes 过滤）
                force_refresh = reconcile_result.get("force_cleared", False)
                shared_memories = self._load_shared_memories(extract_id, force_refresh=force_refresh)

                if shared_memories:
                    # v2.0 写入限制：由 _load_shared_memories 的 SQL LIMIT 控制
                    # 不再在此处标记跳过的为已知（会被 reconcile 当孤儿清除）
                    self._emit("写回 {}: {} 条共享记忆".format(agent_id, len(shared_memories)))

                    # --dry-run: 跳过实际写入，仅统计
                    if self.dry_run:
                        fake_result = WriteBackResult(
                            agent_id=extract_id,
                            target_path=str(target_path),
                            written=len(shared_memories),
                            skipped=0,
                            errors=[],
                            pending=0,
                        )
                        report.writeback_results[agent_id] = fake_result
                        report.total_written += fake_result.written
                        self._emit("  [DRY-RUN] 跳过写入 {} 条".format(len(shared_memories)))
                    else:
                        writer = get_writer(agent_id, self.sync_state)
                        wb_result = writer.write(
                            agent_id=extract_id,
                            target_path=target_path,
                            memories=shared_memories,
                            backup_dir=self.backup_dir,
                        )
                        report.writeback_results[agent_id] = wb_result
                        report.total_written += wb_result.written
                        report.total_skipped += wb_result.skipped

                        if wb_result.errors:
                            report.errors.extend(wb_result.errors)

                        self._emit("  写入 {} 条, 跳过 {} 条".format(
                            wb_result.written, wb_result.skipped
                        ))
                else:
                    self._emit("{}: 无新共享记忆需要写回".format(agent_id))

                # ★ v2.0：始终重建 memory_shared.md（front matter 格式）
                # 这是 Agent 运行手册 v2.0 的要求：每个 Agent 都应有 memory_shared.md
                # 包含其他 Agent 同步过来的记忆，便于 Agent 启动时直接读取。
                # 完全重建模式：从 shared.db 取最新 N 条，确保一致性。
                # 注意：即使无新记忆，也重建以修复可能的格式损坏。
                if not self.dry_run:
                    self._write_shared_md(extract_id)
                    # v2.1.0: 生成精简知识简报，供 Agent 轻量加载
                    self._write_knowledge_brief(extract_id)
                    # v2.1.0: 在 Agent 本地入口注入知识引用（幂等）
                    self._inject_brief_pointer(extract_id)

            # ⑥ 保存去重状态
            self.sync_state.save()

            # ⑦ Phase 0.5: 体积控制（在写回之后执行，防止文件无限膨胀）
            if not self.dry_run:
                self._enforce_volume_control(report)
            else:
                self._emit("[DRY-RUN] 跳过体积控制")

        except Exception as e:
            self.logger.error("同步异常: {}".format(e), exc_info=True)
            report.errors.append("同步异常: {}".format(e))

        finally:
            end_ts = time.time()
            report.end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            report.duration_seconds = end_ts - start_ts

        self._emit("同步完成, 耗时 {:.1f} 秒".format(report.duration_seconds))
        return report

    def _repair_fts_if_needed(self) -> dict:
        """检查并修复数据根下所有 DB 的 FTS 孤儿索引（v2.1.0）。

        历史 bug 导致 memories_fts 中堆积大量孤儿行（id 不在 memories 表），
        数据库可膨胀到 13MB+ 而有效记忆仅 50 条。此方法在同步前检查，
        孤儿比例超过阈值时执行修复 + VACUUM。

        Returns
        -------
        dict
            {"repaired": int, "saved_mb": float}；无孤儿返回 {"repaired": 0}
        """
        try:
            import os as _os
            dbs = list(self.root.glob("*.db")) + list(self.root.glob("agent_*/memories.db"))
            total_repaired = 0
            total_saved = 0.0
            for db_path in dbs:
                try:
                    with MemoryDatabase(db_path) as db:
                        # 表存在性检查
                        has_fts = db.conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE name='memories_fts'"
                        ).fetchone()
                        has_mem = db.conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE name='memories'"
                        ).fetchone()
                        if not has_fts or not has_mem:
                            continue
                        fts_count = db.conn.execute(
                            "SELECT COUNT(*) FROM memories_fts").fetchone()[0]
                        mem_count = db.conn.execute(
                            "SELECT COUNT(*) FROM memories").fetchone()[0]
                        if fts_count <= max(mem_count, 1) * 2:
                            continue  # 正常比例，无需修复
                        size_before = db_path.stat().st_size if db_path.exists() else 0
                        # 删除孤儿行
                        cur = db.conn.execute(
                            """DELETE FROM memories_fts
                               WHERE id NOT IN (SELECT id FROM memories)"""
                        )
                        orphans = cur.rowcount
                        db.conn.commit()
                        if orphans > 0:
                            try:
                                db.conn.execute(
                                    "INSERT INTO memories_fts(memories_fts) VALUES('optimize')")
                                db.conn.commit()
                            except Exception:
                                pass
                            db.conn.close()
                            # VACUUM 需独立连接（事务外）
                            try:
                                import sqlite3
                                conn2 = sqlite3.connect(str(db_path), timeout=60)
                                conn2.execute("VACUUM")
                                conn2.close()
                            except Exception:
                                pass
                            size_after = db_path.stat().st_size if db_path.exists() else 0
                            total_repaired += orphans
                            total_saved += (size_before - size_after) / 1024 / 1024
                except Exception as e:
                    self.logger.warning("FTS 修复失败 {}: {}".format(db_path, e))
            return {"repaired": total_repaired, "saved_mb": total_saved}
        except Exception as e:
            self.logger.warning("FTS 修复扫描失败: {}".format(e))
            return {"repaired": 0, "saved_mb": 0.0}

    def _load_shared_memories(self, agent_id: str, force_refresh: bool = False) -> list:
        """从融合层读取指定 Agent 的共享记忆
        v1.3.7: 增量加载——跳过 SyncState 中已写回的条目，不再 LIMIT 500。
        v2.0: 增加 force_refresh 参数，SyncState 被强制清空后跳过过滤。
        v2.0.1: LIMIT 200，防止目标文件无限膨胀触发污染循环。
               200 条足够覆盖最新记忆，writer 的 dedup 会跳过已写入的。
        """
        shared_db = self.root / "shared.db"
        if not shared_db.exists():
            return []

        memories = []
        known_hashes = set() if force_refresh else self.sync_state.bulk_known_hashes(agent_id)
        try:
            with MemoryDatabase(shared_db) as db:
                cursor = db.conn.execute(
                    "SELECT * FROM memories WHERE agent_id != ? ORDER BY timestamp DESC LIMIT 200",
                    (agent_id,)
                )
                for row in cursor.fetchall():
                    entry = db._row_to_entry(row)
                    # 增量跳过：已在 sync_state 中且 hash 一致的跳过
                    h = content_hash(entry.content)
                    if h in known_hashes:
                        continue
                    memories.append(entry)
        except Exception as e:
            self.logger.warning("读取共享记忆失败: {}".format(e))

        return memories

    # ------------------------------------------------------------------
    # v2.0: memory_shared.md 写入（完全重建模式）
    # ------------------------------------------------------------------
    def _write_shared_md(self, agent_id: str, shared_memories: list = None):
        """完全重建 <agent_dir>/memory_shared.md，从 shared.db 取最新 N 条共享记忆。

        v2.0 设计要点：
        - **完全重建**（非追加）：每次同步都从 shared.db 重新生成，确保一致性
        - 跳过本 Agent 自己写的记忆（agent_id 匹配）
        - 按 timestamp DESC 排序，取前 N 条（受 volume_policy 软限制）
        - 每条记忆以 front matter + 正文格式写入
        - 剥离 sync 标记防止污染

        Parameters
        ----------
        agent_id : str
            当前 Agent ID
        shared_memories : list, optional
            共享记忆列表。若为 None，则从 shared.db 完整加载。
            传入此参数可避免重复加载（如调用方已有列表）。
        """
        agent_dir = self.root / ("agent_" + agent_id)
        if not agent_dir.exists():
            return

        shared_md_path = agent_dir / "memory_shared.md"

        # 加载共享记忆：优先用传入的列表，否则从 shared.db 加载全部
        if shared_memories is None:
            shared_memories = self._load_all_shared_memories(agent_id)

        # 过滤掉本 Agent 自己的记忆
        entries = [m for m in shared_memories if m.agent_id != agent_id]
        if not entries:
            # 无共享记忆：写空文件头
            if not shared_md_path.exists() or shared_md_path.stat().st_size < 50:
                shared_md_path.write_text(
                    "# {} 共享记忆\n\n".format(agent_id),
                    encoding="utf-8",
                )
            return

        # v2.0 软限制：构建时动态检查体积，超限即停
        try:
            policy = self._load_volume_policy()
            limits = policy["limits"]["memory_shared_md"]
            max_lines = limits.get("max_lines", 2000)
            max_size_bytes = limits.get("max_size_kb", 128) * 1024
        except Exception:
            max_lines = 2000
            max_size_bytes = 128 * 1024

        # 构造文件内容（完全重建，动态检查体积）
        from sync_writers import strip_sync_markers

        lines = ["# {} 共享记忆".format(agent_id), ""]
        current_size = len("\n".join(lines).encode("utf-8"))
        written_count = 0
        total_available = len(entries)

        for mem in entries:
            # 构建单条条目
            tags_str = ", ".join('"{}"'.format(t) for t in (mem.tags or []))
            entry_lines = [
                "---",
                "id: {}".format(mem.id),
                "agent_id: {}".format(mem.agent_id),
                "timestamp: {}".format(mem.timestamp),
                "source_device: {}".format(getattr(mem, "source_device", "unknown")),
                "domain: {}".format(mem.domain or "general"),
                "tags: [{}]".format(tags_str),
                "confidence: {}".format(mem.confidence or "medium"),
                "conflict_with: null",
                "---",
            ]
            content = strip_sync_markers(mem.content or "")
            entry_lines.append(content)
            entry_lines.append("")

            # 检查加入此条目后是否超限
            new_lines_count = len(lines) + len(entry_lines)
            new_size = current_size + len(("\n".join(entry_lines) + "\n").encode("utf-8"))

            if new_lines_count > max_lines or new_size > max_size_bytes:
                break  # 超限，停止添加

            lines.extend(entry_lines)
            current_size = new_size
            written_count += 1

        new_text = "\n".join(lines).rstrip() + "\n"

        # 写入
        try:
            from safe_io import _safe_write_text
            if _safe_write_text(shared_md_path, new_text):
                self._emit("  memory_shared.md({}): 重建完成，{} 条（库中共 {} 条）".format(
                    agent_id, written_count, total_available))
            else:
                self._emit("  memory_shared.md({}): 写入失败（权限？）".format(agent_id))
        except Exception as e:
            self.logger.warning("写入 memory_shared.md 失败({}): {}".format(agent_id, e))

    # ------------------------------------------------------------------
    # v2.1.0: knowledge_brief.md 知识提炼层
    # ------------------------------------------------------------------
    _TEMPLATE_NOISE_MARKERS = (
        "BOOTSTRAP.md", "IDENTITY.md", "USER.md", "SOUL.md",
        "_Time to pin down who you are._", "Fill this in during your first conversation",
        "This isn't just metadata", "Save this",
    )

    def _is_template_noise(self, content: str) -> bool:
        """判断记忆是否属于 Agent 模板噪音（无实际知识价值）。"""
        if not content:
            return True
        c = content[:200]
        if any(m in c for m in self._TEMPLATE_NOISE_MARKERS):
            return True
        # front matter 模板
        if c.startswith("---") and ("summary:" in c[:300] or "read_when:" in c[:300]):
            return True
        return False

    def _write_knowledge_brief(self, agent_id: str):
        """生成 <agent_dir>/knowledge_brief.md —— 精简知识摘要。

        设计目标：让 Agent 启动时加载**少量高质量**知识而非数千行原始记忆。
        - 只保留 high/medium confidence、非模板噪音的记忆
        - 按 domain 聚类，每个 domain 保留 top K 条
        - 每条只保留 1-2 句核心要点（首行 + 关键句）
        - 总大小硬限制 ~20KB，保证 Agent 一次加载不爆上下文

        这是 "knowledge_brief.md" 的写入端；Agent 端通过各自入口
        （CLAUDE.md / MEMORY.md / user_profile.md）引入此文件。
        """
        agent_dir = self.root / ("agent_" + agent_id)
        if not agent_dir.exists():
            return

        brief_path = agent_dir / "knowledge_brief.md"

        # 加载全部共享记忆（含本 agent 自己写的——知识库里应包含全量知识）
        shared_db = self.root / "shared.db"
        if not shared_db.exists():
            return

        memories = []
        try:
            with MemoryDatabase(shared_db) as db:
                cursor = db.conn.execute(
                    "SELECT * FROM memories ORDER BY timestamp DESC LIMIT 500")
                for row in cursor.fetchall():
                    memories.append(db._row_to_entry(row))
        except Exception as e:
            self.logger.warning("读取共享记忆(简报)失败: {}".format(e))
            return

        if not memories:
            return

        # 过滤：非模板噪音 + 非低置信度 + 去重
        from sync_writers import strip_sync_markers
        useful = []
        seen_brief = set()
        for m in memories:
            if m.confidence == "low":
                continue
            content = strip_sync_markers(m.content or "").strip()
            if not content or self._is_template_noise(content):
                continue
            # 按首行去重（同一知识反复提取只留最新）
            first_key = content.splitlines()[0][:100] if content.splitlines() else content[:100]
            if first_key in seen_brief:
                continue
            seen_brief.add(first_key)
            useful.append((m, content))

        if not useful:
            return

        # 按 domain 聚类
        from collections import defaultdict
        by_domain = defaultdict(list)
        for m, content in useful:
            by_domain[m.domain or "general"].append((m, content))

        # 每个 domain 保留 top K 条（按 confidence 排序）
        conf_rank = {"high": 3, "medium": 2, "low": 1}
        MAX_PER_DOMAIN = 15
        MAX_TOTAL = 60
        selected = []
        for domain, items in by_domain.items():
            items.sort(key=lambda x: (conf_rank.get(x[0].confidence, 1), x[0].timestamp), reverse=True)
            selected.extend(items[:MAX_PER_DOMAIN])
        # 全局截断
        selected.sort(key=lambda x: (conf_rank.get(x[0].confidence, 1), x[0].timestamp), reverse=True)
        selected = selected[:MAX_TOTAL]

        # 提取要点：首行 + 去重 + 压缩空白
        def _first_key_line(text: str) -> str:
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            for ln in lines:
                if ln.startswith("#"):
                    continue
                # 跳过 front matter
                if ln in ("---",):
                    continue
                return ln[:120]
            return ""

        lines = [
            "# 知识简报 (Knowledge Brief)",
            "",
            "> 自动生成于 {}，来源: 各 Agent 共享记忆融合层".format(
                datetime.now().strftime("%Y-%m-%d %H:%M")),
            "> 精简提取 top {} 条（按置信度），完整记录见 memory_shared.md".format(len(selected)),
            "",
        ]

        by_domain_out = defaultdict(list)
        for m, content in selected:
            by_domain_out[m.domain or "general"].append((m, content))

        for domain in sorted(by_domain_out.keys()):
            items = by_domain_out[domain]
            lines.append("## {}".format(domain))
            for m, content in items:
                key = _first_key_line(content)
                if not key:
                    continue
                src = m.agent_id
                lines.append("- [{}] {}".format(src, key))
            lines.append("")

        brief_text = "\n".join(lines).rstrip() + "\n"

        # 体积硬限制 20KB
        if len(brief_text.encode("utf-8")) > 20 * 1024:
            brief_text = brief_text[: 20 * 1024].rsplit("\n", 1)[0] + "\n"

        try:
            from safe_io import _safe_write_text
            if _safe_write_text(brief_path, brief_text):
                self._emit("  knowledge_brief.md({}): 生成 {} 条知识要点 ({:.1f}KB)".format(
                    agent_id, len(selected), len(brief_text.encode("utf-8")) / 1024))
        except Exception as e:
            self.logger.warning("写入 knowledge_brief.md 失败({}): {}".format(agent_id, e))

    def _inject_brief_pointer(self, agent_id: str):
        """在 Agent 本地入口文件中注入 knowledge_brief.md 引用（v2.1.0）。

        目标：让 Agent 启动时**主动加载**知识简报（而非依赖人肉配置）。
        对每个已检测到的 Agent 本地记忆文件，追加一小节引导说明：

            ## Shared Knowledge (auto-synced)
            ... 读取 {data_root}/agent_{id}/knowledge_brief.md ...

        幂等：已存在同标记则跳过，避免重复追加。
        """
        try:
            from safe_io import _safe_read_text, _safe_write_text
            from safe_io import get_data_root
            brief_rel = "agent_{}/knowledge_brief.md".format(agent_id)
            brief_abs = self.root / brief_rel
            if not brief_abs.exists():
                return

            # 找到该 agent 的本地记忆文件（复用 detect_agents 的路径信息）
            detected = detect_agents(self.config, force_redetect=False)
            info = detected.get(agent_id) or detected.get(agent_id.replace("-appdata", ""))
            if not info:
                return

            target_dir = Path(info["path"])
            if not target_dir.exists():
                return

            # 候选入口文件
            candidates = []
            if info.get("memory_files"):
                candidates = [Path(f) for f in info["memory_files"] if f.endswith(".md")]
            if not candidates:
                candidates = [
                    target_dir / "MEMORY.md",
                    target_dir / "user_profile.md",
                    target_dir / "memory" / "MEMORY.md",
                ]
            # 只处理存在的文件，且避免把引用写进超大文件（>2MB 跳过）
            entry_file = None
            for cand in candidates:
                try:
                    if cand.exists() and cand.stat().st_size < 2 * 1024 * 1024:
                        entry_file = cand
                        break
                except OSError:
                    continue
            if entry_file is None:
                return

            marker = "<!-- agent-memory:knowledge-brief -->"
            existing = _safe_read_text(entry_file, default="")
            if marker in existing:
                return  # 已注入，跳过

            brief_ref = """

## Shared Knowledge (auto-synced)

<!-- agent-memory:knowledge-brief -->
系统已自动为你生成本机各 Agent 的共享知识简报。
请在每次会话开始时读取以下文件获取最新跨 Agent 记忆要点：

- 精简知识简报（推荐，约 3KB）: {abs}
- 完整共享记忆（约 60-80KB）: {md}

格式：front matter + Markdown，可由解析器直接读取。
""".format(
                abs=Path(get_data_root()) / brief_rel,
                md=Path(get_data_root()) / ("agent_{}/memory_shared.md".format(agent_id)),
            )

            new_content = existing.rstrip() + brief_ref
            if _safe_write_text(entry_file, new_content):
                self._emit("  📌 已注入共享知识引用 -> {}".format(entry_file))
        except Exception as e:
            self.logger.warning("注入知识引用失败({}): {}".format(agent_id, e))

    def _load_all_shared_memories(self, agent_id: str) -> list:
        """从 shared.db 加载指定 Agent 的所有共享记忆（排除自己的）。

        与 _load_shared_memories 不同，此方法不过滤 sync_state，
        用于完全重建 memory_shared.md。

        Returns
        -------
        list
            按 timestamp DESC 排序的 MemoryEntry 列表
        """
        shared_db = self.root / "shared.db"
        if not shared_db.exists():
            return []

        memories = []
        try:
            with MemoryDatabase(shared_db) as db:
                cursor = db.conn.execute(
                    "SELECT * FROM memories WHERE agent_id != ? ORDER BY timestamp DESC LIMIT 2000",
                    (agent_id,)
                )
                for row in cursor.fetchall():
                    entry = db._row_to_entry(row)
                    memories.append(entry)
        except Exception as e:
            self.logger.warning("读取全部共享记忆失败({}): {}".format(agent_id, e))

        return memories

    # ------------------------------------------------------------------
    # v2.0.1: 污染条目清理
    # ------------------------------------------------------------------
    def _purge_polluted_entries(self) -> dict:
        """扫描所有 agent_*.db 和 shared.db，删除污染条目。

        污染判定（复用 agent_memory._is_sync_generated_content）：
        - 含 [sync:...] 标记
        - 含 RAW_JSON_START/END
        - 嵌套 2+ 个 "— 来自 xxx (date)" 标记
        - 其他 sync 产物特征

        v2.0.2: 同时清理 memory_private.md / memory_shared.md 中的污染条目块。
        完全重建模式：从干净的 DB 重建 .md 文件。

        Returns
        -------
        dict
            {purged, dbs_scanned, md_cleaned, details: {db_name: count}}
        """
        from agent_memory import _is_sync_generated_content

        result = {"purged": 0, "dbs_scanned": 0, "md_cleaned": 0, "details": {}}

        # 扫描所有 agent_*/memories.db 和 shared.db
        db_paths = []
        agent_dirs = []
        if self.root.exists():
            shared_db = self.root / "shared.db"
            if shared_db.exists():
                db_paths.append(("shared.db", shared_db))
            for sub in self.root.iterdir():
                if sub.is_dir() and sub.name.startswith("agent_"):
                    agent_dirs.append(sub)
                    db = sub / "memories.db"
                    if db.exists():
                        db_paths.append(("agent db: " + sub.name, db))

        result["dbs_scanned"] = len(db_paths)

        # 阶段 1: 清理 DB
        for db_label, db_path in db_paths:
            purged_in_db = 0
            try:
                with MemoryDatabase(db_path) as db:
                    cursor = db.conn.execute(
                        "SELECT id, content FROM memories"
                    )
                    rows = cursor.fetchall()
                    ids_to_delete = []
                    for row in rows:
                        mem_id = row[0]
                        content = row[1] or ""
                        if _is_sync_generated_content(content):
                            ids_to_delete.append(mem_id)

                    if ids_to_delete:
                        # 分批删除，每批 100 条（v2.1.0: 同步清理 FTS 索引）
                        for i in range(0, len(ids_to_delete), 100):
                            batch = ids_to_delete[i:i + 100]
                            db.delete_memories(batch)
                        purged_in_db = len(ids_to_delete)

                if purged_in_db > 0:
                    result["purged"] += purged_in_db
                    result["details"][db_label] = purged_in_db
                    self._emit("  {} 清理: {} 条污染".format(db_label, purged_in_db))
            except Exception as e:
                self.logger.warning("清理 {} 污染失败: {}".format(db_label, e))

        # 阶段 2: 清理 memory_private.md 中的污染条目块（front matter 格式）
        # 只在有 DB 清理动作时执行，避免无谓的文件操作
        if result["purged"] > 0:
            result["md_cleaned"] = self._clean_md_files(agent_dirs)

        return result

    def _clean_md_files(self, agent_dirs: list) -> int:
        """清理 memory_private.md / memory_shared.md 中的污染条目块。

        使用 front matter 解析（与 shrink_memory_files.py 一致），
        对每个条目的 body 调用 _is_sync_generated_content 判断是否为污染。
        污染条目整块删除，干净条目保留。

        Parameters
        ----------
        agent_dirs : list
            agent_* 目录的 Path 列表

        Returns
        -------
        int
            清理的条目总数
        """
        import re
        import sys as _sys
        from agent_memory import _is_sync_generated_content

        # 复用 shrink_memory_files 的解析函数
        tools_dir = Path(__file__).parent / "tools"
        if str(tools_dir) not in _sys.path:
            _sys.path.insert(0, str(tools_dir))
        try:
            from shrink_memory_files import parse_memory_entries, format_entry
        except ImportError:
            self.logger.warning("无法导入 shrink_memory_files 解析函数，跳过 .md 清理")
            return 0

        FRONT_MATTER_RE = re.compile(
            r"^---\s*$\n(.*?)\n^---\s*$\n(.*?)(?=^---\s*$\n|\Z)",
            re.MULTILINE | re.DOTALL,
        )

        total_cleaned = 0
        for agent_dir in agent_dirs:
            for fname in ("memory_private.md", "memory_shared.md"):
                fp = agent_dir / fname
                if not fp.exists():
                    continue

                try:
                    text = fp.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                # 保留头部（第一个 --- 之前的内容）
                first_match = re.search(r"^---\s*$\n", text, re.MULTILINE)
                if not first_match:
                    continue  # 无 front matter，跳过
                header = text[:first_match.start()]

                entries = parse_memory_entries(text)
                if not entries:
                    continue

                clean_entries = []
                polluted_count = 0
                for fm, body in entries:
                    if _is_sync_generated_content(body):
                        polluted_count += 1
                    else:
                        clean_entries.append((fm, body))

                if polluted_count == 0:
                    continue

                # 重建文件：header + 干净条目
                new_lines = [header.rstrip()] if header.strip() else []
                for fm, body in clean_entries:
                    new_lines.append(format_entry(fm, body))
                new_text = "\n".join(new_lines).rstrip() + "\n"

                try:
                    from safe_io import _safe_write_text
                    if _safe_write_text(fp, new_text):
                        total_cleaned += polluted_count
                        self._emit("  {} {}: 清理 {} 条污染".format(
                            agent_dir.name, fname, polluted_count))
                except Exception as e:
                    self.logger.warning("写入 {} 失败: {}".format(fp, e))

        return total_cleaned

    # ------------------------------------------------------------------
    # Phase 0.5: 体积控制
    # ------------------------------------------------------------------
    _DEFAULT_VOLUME_POLICY = {
        "limits": {
            "memory_private_md": {
                "max_lines": 3000,
                "max_size_kb": 256,
            },
            "memory_shared_md": {
                "max_lines": 2000,
                "max_size_kb": 128,
            },
            "shared_db": {
                "max_total_entries": 5000,
                "max_age_days": 365,
            },
        },
        "expiration": {
            "default_ttl_days": 180,
            "high_confidence_ttl_days": 365,
            "low_confidence_ttl_days": 90,
            "never_expire_tags": ["core_identity", "permanent", "用户身份", "用户明确指令"],
        },
    }

    def _load_volume_policy(self) -> dict:
        """加载体积策略，优先用 _shared/volume_policy.json，否则用内置默认。"""
        policy_path = self.root / "_shared" / "volume_policy.json"
        if policy_path.exists():
            try:
                import json
                return json.loads(policy_path.read_text(encoding="utf-8"))
            except Exception as e:
                self.logger.warning("加载 volume_policy.json 失败: {}".format(e))
        return self._DEFAULT_VOLUME_POLICY

    def _enforce_volume_control(self, report: SyncReport):
        """执行体积控制：对超限的 memory_*.md 文件压缩/截断；对 shared.db 过期清理。"""
        policy = self._load_volume_policy()
        self._emit("\n📦 体积控制:")

        # 1. memory_private.md / memory_shared.md 体积控制
        # 复用 tools/shrink_memory_files.py 的 shrink_file 函数
        try:
            import sys as _sys
            tools_dir = Path(__file__).parent / "tools"
            if str(tools_dir) not in _sys.path:
                _sys.path.insert(0, str(tools_dir))
            from shrink_memory_files import shrink_file
        except ImportError as e:
            self._emit("  ⚠ 无法导入 shrink_file: {}".format(e))
            return

        limits_private = policy["limits"]["memory_private_md"]
        limits_shared = policy["limits"]["memory_shared_md"]

        for agent_id in report.agents_detected.keys():
            extract_id = agent_id.replace("-appdata", "")
            agent_dir = self.root / ("agent_" + extract_id)
            if not agent_dir.exists():
                continue

            for fname, limits in [
                ("memory_private.md", limits_private),
                ("memory_shared.md", limits_shared),
            ]:
                fp = agent_dir / fname
                if not fp.exists():
                    continue

                try:
                    r = shrink_file(
                        fp,
                        max_lines=limits.get("max_lines", 3000),
                        max_size_kb=limits.get("max_size_kb", 256),
                        dry_run=False,
                    )
                except Exception as e:
                    self._emit("  {} {}: 失败 - {}".format(agent_id, fname, e))
                    continue

                if r.get("error"):
                    self._emit("  {} {}: 错误 - {}".format(agent_id, fname, r["error"]))
                elif r["action"] in ("shrunk", "force_truncated"):
                    self._emit("  {} {}: {} → {} 行, {} → {} KB ({})".format(
                        agent_id, fname,
                        r["before_lines"], r["after_lines"],
                        r["before_size_kb"], r["after_size_kb"],
                        r["action"],
                    ))
                # action == "ok" / "skipped_no_entries" 不打印

        # 2. shared.db 过期清理 + VACUUM
        shared_db_path = self.root / "shared.db"
        if shared_db_path.exists():
            try:
                db_result = self._enforce_db_limit(shared_db_path, policy)
                if db_result.get("expired", 0) > 0:
                    self._emit("  shared.db: 过期 {} 条, 已 VACUUM 回收".format(
                        db_result["expired"]))
                elif db_result.get("vacuumed"):
                    self._emit("  shared.db: 已 VACUUM (无过期条目)")
            except Exception as e:
                self._emit("  shared.db 体积控制失败: {}".format(e))

    def _enforce_db_limit(self, db_path: Path, policy: dict) -> dict:
        """对 shared.db 执行过期清理和 VACUUM 回收。"""
        result = {"expired": 0, "vacuumed": False}
        if not db_path.exists():
            return result

        exp_config = policy.get("expiration", {})
        limits = policy["limits"].get("shared_db", {})
        max_entries = limits.get("max_total_entries", 5000)

        try:
            with MemoryDatabase(db_path) as db:
                total = db.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

                if total > max_entries:
                    # 阶段 1: 过期 low confidence 旧条目
                    # v2.1.0: 同步清理 FTS 索引（memories_fts 是独立表，需按 id 同步删）
                    ttl_low = exp_config.get("low_confidence_ttl_days", 90)
                    expired_ids = db.conn.execute(
                        """SELECT id FROM memories
                           WHERE confidence = 'low'
                             AND timestamp < datetime('now', '-' || ? || ' days')""",
                        (ttl_low,)
                    ).fetchall()
                    ids1 = [r[0] for r in expired_ids]
                    if ids1:
                        db.delete_memories(ids1)
                    result["expired"] += len(ids1)

                    # 阶段 2: 仍超限 → 过期 medium + low 超过 default_ttl
                    remaining = total - result["expired"]
                    if remaining > max_entries:
                        ttl_default = exp_config.get("default_ttl_days", 180)
                        expired_ids2 = db.conn.execute(
                            """SELECT id FROM memories
                               WHERE confidence IN ('low', 'medium')
                                 AND timestamp < datetime('now', '-' || ? || ' days')""",
                            (ttl_default,)
                        ).fetchall()
                        ids2 = [r[0] for r in expired_ids2]
                        if ids2:
                            db.delete_memories(ids2)
                        result["expired"] += len(ids2)

                # VACUUM 回收空间（必须在事务外执行）
                try:
                    db.conn.execute("VACUUM")
                    result["vacuumed"] = True
                except Exception as e:
                    self.logger.warning("VACUUM 失败: {}".format(e))

        except Exception as e:
            self.logger.warning("shared.db 体积控制失败: {}".format(e))

        return result

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
