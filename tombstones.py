"""
墓碑（Tombstone）机制 —— 防止已删除的共享记忆跨设备复活
========================================================

问题（P1-3 审计确认的复活链）：
    用户/Agent 直接编辑记忆文件删除一条已同步的记忆后：
      1. 目标文件中该内容的 hash marker 消失
      2. reconcile_with_target_hashes（正常模式）把该 hash 当孤儿从
         SyncState 清除
      3. 下一轮写回时 known_hashes 不再包含它
      4. shared.db 里仍存在的同内容条目被重新写回 → 删除被"复活"
    （删除从不传播：sync_agent_to_shared 只 upsert，sync_shared_to_agent
      只插入，共享层永远不知道"某条记忆已被删除"。）

方案：
    - reconcile 正常模式检测到 vanish（tracked hash 从目标文件消失）时，
      把 content_hash 写入墓碑库。墓碑库存放在数据根（OneDrive 同步，
      跨设备生效）。
    - 所有"从共享层读取并写回"的路径过滤墓碑命中的内容：
      * sync_engine._load_shared_memories（增量写回）
      * sync_engine._load_all_shared_memories（memory_shared.md 重建）
      * agent_memory.MemoryMerger.sync_shared_to_agent（DB 级融合）
    - 每轮同步在融合后、写回前，把 shared.db 中命中的行清除（治本，
      同时保证 memory_shared.md 重建路径不再复活）。

安全阀（防误杀）：
    - 保守模式（目标文件不存在 / legacy-only）绝不产生墓碑 —— 挂点
      在 reconcile 的"正常模式"分支，保守分支提前 return。
    - 宽限期：hash 写入 SyncState 不足 TOMBSTONE_GRACE_SECONDS 就消失
      的，视为 pending / 写入失败等瞬时状态，不产生墓碑（仅清 state，
      保持旧行为）。
    - 墓碑任何环节失败都不阻断主同步流程（全部 try/except 包裹）。

存储格式（.tombstones.json）：
    {
      "version": 1,
      "tombstones": {
        "<content_hash>": {
          "agent_id": "claude",          # 最后一个跟踪它的 agent
          "deleted_at": "2026-08-29T...",
          "reason": "reconcile_vanish"
        }
      }
    }

保留策略：默认永久保留（keep_days=0）。墓碑体量极小（每条 ~100 字节），
永久保留的防复活收益远大于磁盘成本。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Set

TOMBSTONE_VERSION = 1
# vanish 宽限期：写入 state 后不足该时长就消失的 hash 不墓碑化
TOMBSTONE_GRACE_SECONDS = 24 * 3600


class TombstoneStore:
    """已删除记忆的墓碑库（JSON 存储，数据根 OneDrive 同步）。"""

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            from safe_io import get_data_root
            path = get_data_root() / ".tombstones.json"
        self.path = Path(path)
        self._entries: Optional[dict] = None  # 惰性加载缓存

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def _read_disk(self) -> dict:
        """从磁盘读墓碑（不做内存缓存，供加锁合并用）。"""
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        # 新格式 {"version":1,"tombstones":{...}}；兼容裸 {hash: meta} 旧格式
        t = data.get("tombstones") if "tombstones" in data else data
        return t if isinstance(t, dict) else {}

    def _load(self) -> dict:
        if self._entries is None:
            self._entries = self._read_disk()
        return self._entries

    # ------------------------------------------------------------------
    # 写入（FileLock + 读盘合并 + 原子写，模式与 SyncState.save 一致）
    # ------------------------------------------------------------------
    def add(self, hashes: Iterable[str], agent_id: str = "", reason: str = "") -> int:
        """批量记录墓碑，返回新增条数。任何失败都不抛出（尽力而为）。"""
        hashes = [h for h in (hashes or []) if h]
        if not hashes:
            return 0
        added = 0
        try:
            from agent_memory import FileLock
            from safe_io import _safe_write_text
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.path.with_name(self.path.name + ".lock")
            with FileLock(lock_path):
                disk = self._read_disk()
                now = datetime.now(timezone.utc).isoformat()
                for h in hashes:
                    if h not in disk:
                        disk[h] = {
                            "agent_id": agent_id,
                            "deleted_at": now,
                            "reason": reason,
                        }
                        added += 1
                self._entries = disk
                content = json.dumps(
                    {"version": TOMBSTONE_VERSION, "tombstones": disk},
                    ensure_ascii=False, indent=2, sort_keys=True,
                )
                _safe_write_text(self.path, content)
        except Exception:
            # 写失败：丢弃内存缓存，下次重新从盘上读（可能丢本次新增，可接受）
            self._entries = None
        return added

    def prune(self, keep_days: int = 0) -> int:
        """按 deleted_at 清理过期墓碑。keep_days<=0 表示永久保留。"""
        if keep_days <= 0:
            return 0
        entries = self._load()
        now = datetime.now(timezone.utc)
        expired = []
        for h, meta in entries.items():
            try:
                t0 = datetime.fromisoformat(str(meta.get("deleted_at", "")))
                if t0.tzinfo is None:
                    t0 = t0.replace(tzinfo=timezone.utc)
                if (now - t0).total_seconds() > keep_days * 86400:
                    expired.append(h)
            except ValueError:
                continue
        if not expired:
            return 0
        for h in expired:
            entries.pop(h, None)
        try:
            from agent_memory import FileLock
            from safe_io import _safe_write_text
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(self.path.with_name(self.path.name + ".lock")):
                content = json.dumps(
                    {"version": TOMBSTONE_VERSION, "tombstones": entries},
                    ensure_ascii=False, indent=2, sort_keys=True,
                )
                _safe_write_text(self.path, content)
            return len(expired)
        except Exception:
            self._entries = None
            return 0

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def is_tombstoned(self, content_hash_value: str) -> bool:
        if not content_hash_value:
            return False
        return content_hash_value in self._load()

    def known(self, hashes: Set[str]) -> Set[str]:
        """返回入参中命中墓碑的 hash 子集。"""
        if not hashes:
            return set()
        entries = self._load()
        return set(hashes) & set(entries.keys())

    @property
    def count(self) -> int:
        return len(self._load())

    # ------------------------------------------------------------------
    # 治本：从 SQLite 记忆库删除命中墓碑的行
    # ------------------------------------------------------------------
    def purge_db(self, db_path) -> int:
        """删除 SQLite 记忆库中内容命中墓碑的行，返回删除行数。

        同时清理 memories_fts（若存在），避免 FTS 索引残留孤儿行。
        库不存在 / 无墓碑 / 任何失败都返回 0，不抛异常。
        """
        entries = self._load()
        if not entries or not db_path:
            return 0
        db_path = Path(db_path)
        if not db_path.exists():
            return 0
        try:
            import sqlite3
            from agent_memory import content_hash
        except Exception:
            return 0

        conn = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=60)
            rows = conn.execute("SELECT id, content FROM memories").fetchall()
            hit_ids = [rid for rid, c in rows if content_hash(c or "") in entries]
            if not hit_ids:
                return 0
            placeholders = ",".join("?" for _ in hit_ids)
            try:
                conn.execute(
                    "DELETE FROM memories_fts WHERE id IN ({})".format(placeholders),
                    hit_ids,
                )
            except sqlite3.OperationalError:
                pass  # 无 FTS 表
            conn.execute(
                "DELETE FROM memories WHERE id IN ({})".format(placeholders),
                hit_ids,
            )
            conn.commit()
            return len(hit_ids)
        except Exception:
            return 0
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# 模块级单例（供过滤路径使用；reconcile 路径用 SyncState 自带实例）
# ---------------------------------------------------------------------------
_store: Optional[TombstoneStore] = None


def get_tombstone_store() -> TombstoneStore:
    global _store
    if _store is None:
        _store = TombstoneStore()
    return _store


def reset_tombstone_store() -> None:
    """重置单例（测试用，或数据根切换后强制重读）。"""
    global _store
    _store = None
