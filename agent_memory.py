"""
Agent 记忆系统 - MVP 版本
=========================
实现设计文档 v1.2 的:
  - 启动流程 (第 5.1 节, 全部 8 步)
  - 写入流程 (第 5.2 节, 落盘 7 步, 无缓冲/无节流/无去重)

所有路径使用 pathlib.Path, 不使用字符串拼接。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import logging
import logging.handlers
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from safe_io import _safe_write_text, get_data_root


# ---------------------------------------------------------------------------
# 配置管理器 - 新增
# ---------------------------------------------------------------------------

class ConfigManager:
    """配置管理器"""

    DEFAULT_CONFIG = {
        "version": "1.0.0",
        "paths": {
            "memory_root": "auto",
            "shared_root": "auto",
            "backup_dir": ".backups",
            "archive_dir": ".archive",
            "lock_dir": ".locks",
            "log_dir": ".logs"
        },
        "limits": {
            "max_memories_per_agent": 10000,
            "max_backups": 10,
            "max_memory_age_days": 365,
            "hot_tier_days": 30,
            "warm_tier_days": 180,
            "cold_tier_days": 365,
            "id_overflow_limit": 999
        },
        "compression": {
            "similarity_threshold": 0.7,
            "min_importance_score": 0.3,
            "target_count": None,
            "auto_compress_threshold": 5000
        },
        "security": {
            "sensitive_patterns": [
                "password", "密码", "secret", "密钥", "token", "令牌",
                "api_key", "apikey", "private_key", "私钥",
                "credential", "凭据", "auth", "认证"
            ],
            "warn_on_sensitive": True,
            "block_sensitive": False
        },
        "logging": {
            "level": "INFO",
            "max_log_size_mb": 10,
            "max_log_files": 5,
            "log_format": "%(asctime)s [%(levelname)s] %(message)s"
        },
        "database": {
            "version": 1,
            "auto_vacuum": True,
            "wal_mode": True,
            "cache_size": 1000
        },
        "sync": {
            "conflict_strategy": "newer_wins",
            "retry_count": 3,
            "retry_delay_seconds": 1,
            "lock_timeout_seconds": 30
        }
    }

    def __init__(self, config_path: Path = None):
        """
        初始化配置管理器

        Parameters
        ----------
        config_path : Path, optional
            配置文件路径，默认为模块目录下的 config.json
        """
        if config_path is None:
            config_path = Path(__file__).parent / "config.json"

        self.config_path = config_path
        self.config = self.DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        """加载配置文件"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                self._merge_config(self.config, user_config)
            except Exception as e:
                logging.warning("加载配置文件失败: {}".format(e))

    def _merge_config(self, base: dict, override: dict):
        """递归合并配置"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_config(base[key], value)
            else:
                base[key] = value

    def save(self):
        """保存配置文件"""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error("保存配置文件失败: {}".format(e))

    def get(self, key_path: str, default=None):
        """
        获取配置值

        Parameters
        ----------
        key_path : str
            配置路径，如 "paths.memory_root"
        default : any
            默认值

        Returns
        -------
        any
            配置值
        """
        keys = key_path.split(".")
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def set(self, key_path: str, value):
        """
        设置配置值

        Parameters
        ----------
        key_path : str
            配置路径
        value : any
            配置值
        """
        keys = key_path.split(".")
        config = self.config
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        config[keys[-1]] = value


# 全局配置实例
_config_manager = None


def get_config() -> ConfigManager:
    """获取全局配置管理器"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


# ---------------------------------------------------------------------------
# 日志系统 - 新增
# ---------------------------------------------------------------------------

class LogManager:
    """日志管理器"""

    def __init__(self, log_dir: Path = None, config: ConfigManager = None):
        """
        初始化日志管理器

        Parameters
        ----------
        log_dir : Path, optional
            日志目录
        config : ConfigManager, optional
            配置管理器
        """
        if config is None:
            config = get_config()

        if log_dir is None:
            log_dir = Path(__file__).parent / config.get("paths.log_dir", ".logs")

        self.log_dir = log_dir
        self.log_dir.mkdir(exist_ok=True)

        self.logger = logging.getLogger("AgentMemory")
        self.logger.setLevel(getattr(logging, config.get("logging.level", "INFO")))

        # 文件处理器（带轮转）
        log_file = self.log_dir / "agent_memory.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=config.get("logging.max_log_size_mb", 10) * 1024 * 1024,
            backupCount=config.get("logging.max_log_files", 5),
            encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(
            config.get("logging.log_format", "%(asctime)s [%(levelname)s] %(message)s")
        ))
        self.logger.addHandler(file_handler)

        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        self.logger.addHandler(console_handler)

    def get_logger(self) -> logging.Logger:
        """获取日志记录器"""
        return self.logger


# 全局日志实例
_log_manager = None
_logger = None


def get_logger() -> logging.Logger:
    """获取全局日志记录器"""
    global _log_manager, _logger
    if _logger is None:
        if _log_manager is None:
            _log_manager = LogManager()
        _logger = _log_manager.get_logger()
    return _logger


# ---------------------------------------------------------------------------
# 敏感信息检测器 - 新增
# ---------------------------------------------------------------------------

class SensitiveInfoDetector:
    """敏感信息检测器"""

    def __init__(self, config: ConfigManager = None):
        """
        初始化敏感信息检测器

        Parameters
        ----------
        config : ConfigManager, optional
            配置管理器
        """
        if config is None:
            config = get_config()

        self.patterns = config.get("security.sensitive_patterns", [])
        self.warn_on_sensitive = config.get("security.warn_on_sensitive", True)
        self.block_sensitive = config.get("security.block_sensitive", False)

        # 编译正则表达式
        self._regex_patterns = []
        for pattern in self.patterns:
            try:
                self._regex_patterns.append(re.compile(pattern, re.IGNORECASE))
            except:
                pass

    def check(self, text: str) -> dict:
        """
        检查文本中是否包含敏感信息

        Parameters
        ----------
        text : str
            要检查的文本

        Returns
        -------
        dict
            检查结果
        """
        result = {
            "has_sensitive": False,
            "matches": [],
            "blocked": False
        }

        for pattern in self._regex_patterns:
            matches = pattern.findall(text)
            if matches:
                result["has_sensitive"] = True
                result["matches"].extend(matches)

        if result["has_sensitive"]:
            if self.warn_on_sensitive:
                get_logger().warning("检测到敏感信息: {}".format(result["matches"]))

            if self.block_sensitive:
                result["blocked"] = True
                get_logger().error("写入被阻止: 包含敏感信息")

        return result

    def sanitize(self, text: str) -> str:
        """
        清理文本中的敏感信息

        Parameters
        ----------
        text : str
            原始文本

        Returns
        -------
        str
            清理后的文本
        """
        result = text
        for pattern in self._regex_patterns:
            result = pattern.sub("[REDACTED]", result)
        return result


# 全局检测器实例
_detector = None


def get_detector() -> SensitiveInfoDetector:
    """获取全局敏感信息检测器"""
    global _detector
    if _detector is None:
        _detector = SensitiveInfoDetector()
    return _detector


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Identity:
    """Agent 身份标识与路径配置 (对应 identity.json)"""
    agent_id: str
    display_name: str
    primary_domain: str
    memory_root: Path
    shared_root: Path
    source_device: str
    created_at: str


@dataclass
class MemoryEntry:
    """单条记忆条目"""
    id: str
    agent_id: str
    timestamp: str
    source_device: str
    domain: str
    tags: list
    confidence: str
    conflict_with: Optional[str]
    content: str
    # 新增字段 - 向量搜索支持
    embedding: Optional[bytes] = None  # 序列化的向量 (numpy.ndarray.tobytes())
    access_count: int = 0  # 访问次数
    last_accessed: Optional[str] = None  # 最后访问时间 (ISO 8601)
    source_memory_id: Optional[str] = None  # 共享记忆的来源ID


@dataclass
class SyncStatus:
    """同步状态 (对应 last_sync.json)"""
    agent_id: str
    last_merge_timestamp: Optional[str]
    last_merge_id: Optional[str]
    shared_memory_version: str


@dataclass
class StartupContext:
    """启动流程返回的完整上下文"""
    identity: Identity
    runtime_manual: str
    policy: str
    private_memories: list
    shared_memories: list
    write_allowed: bool = True


# ---------------------------------------------------------------------------
# 全局启动上下文 (模块级单例, 供 write_memory 使用)
# ---------------------------------------------------------------------------

_context = None


def get_loaded_identity():
    """获取已加载的 Identity, 若未启动则抛出异常"""
    if _context is None:
        raise RuntimeError("Agent 未启动, 请先调用 startup()")
    return _context.identity


def get_loaded_context():
    """获取完整的启动上下文"""
    if _context is None:
        raise RuntimeError("Agent 未启动, 请先调用 startup()")
    return _context


# ---------------------------------------------------------------------------
# 异常定义
# ---------------------------------------------------------------------------

class AgentMemoryError(Exception):
    """基础异常"""
    pass


class IdentityNotFoundError(AgentMemoryError):
    """identity.json 不存在"""
    pass


class DeviceConfigNotFoundError(AgentMemoryError):
    """device_config.json 不存在"""
    pass


class PolicyValidationError(AgentMemoryError):
    """写入内容不符合 writing_policy.md"""
    pass


class LockError(AgentMemoryError):
    """文件锁获取失败"""
    pass


class VerifyError(AgentMemoryError):
    """写入自检失败"""
    pass


class MemoryIDOverflow(AgentMemoryError):
    """ID 序号溢出 (超过 999)"""
    pass


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def now_iso8601():
    """返回当前时间的 ISO 8601 格式字符串, 精确到秒, 含时区信息"""
    from datetime import timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_YYYYMMDD():
    """返回当前日期字符串 YYYYMMDD"""
    return datetime.now().strftime("%Y%m%d")


def fsync_file(file_path):
    """强制将文件刷盘 (fsync), 兼容 Windows 文件锁定场景"""
    try:
        fd = os.open(str(file_path), os.O_RDWR)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except PermissionError:
        # Windows 上文件可能被 OneDrive 同步锁定, 降级为无 fsync
        import warnings
        warnings.warn("fsync 失败 (PermissionError), 文件可能未完全刷盘: {}".format(file_path))


def read_file_if_exists(file_path):
    """读取文件内容, 文件不存在则返回空字符串"""
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# 文件锁 (上下文管理器)
# ---------------------------------------------------------------------------

@contextmanager
def FileLock(lock_path):
    """
    文件锁上下文管理器。

    死锁判断阈值: 30 秒。
    (融合器的 .merge_lock 阈值为 1 小时, 但 Agent 写记忆的锁粒度更小,
     超过 30 秒即视为死锁。)
    """
    lock_fd = None
    try:
        # 死锁检测: 检查已有锁是否超时
        if lock_path.exists():
            try:
                lock_time_str = lock_path.read_text(encoding="utf-8").strip()
                lock_time = datetime.fromisoformat(lock_time_str)
                if datetime.now() - lock_time > timedelta(seconds=30):
                    lock_path.unlink()
                else:
                    raise LockError(
                        "Cannot acquire lock: {} (locked at {})".format(
                            lock_path, lock_time_str)
                    )
            except (ValueError, OSError):
                lock_path.unlink()

        # 原子创建锁文件 (O_CREAT | O_EXCL 防止 TOCTOU 竞态)
        try:
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(lock_fd, datetime.now().isoformat().encode("utf-8"))
            os.fsync(lock_fd)
        except FileExistsError:
            raise LockError("Cannot acquire lock: {} (already exists)".format(lock_path))
        lock_acquired = True
        yield lock_path

    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        if lock_acquired and lock_path.exists():
            lock_path.unlink()


# ---------------------------------------------------------------------------
# 记忆条目解析与格式化
# ---------------------------------------------------------------------------

_ENTRY_PATTERN = re.compile(
    r"^---\s*\n"
    r"(.*?)"
    r"^---\s*\n"
    r"(.*?)"
    r"(?=^---\s*$|\Z)",
    re.MULTILINE | re.DOTALL
)


def parse_memories(md_text):
    """从 Markdown 文本中解析所有记忆条目"""
    entries = []
    if not md_text.strip():
        return entries

    for match in _ENTRY_PATTERN.finditer(md_text):
        front_matter_raw = match.group(1).strip()
        content = match.group(2).strip()

        fm = {}
        for line in front_matter_raw.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if value.startswith("[") and value.endswith("]"):
                    value = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
                elif value.lower() == "null":
                    value = None
                fm[key] = value

        entry = MemoryEntry(
            id=fm.get("id", ""),
            agent_id=fm.get("agent_id", ""),
            timestamp=fm.get("timestamp", ""),
            source_device=fm.get("source_device", ""),
            domain=fm.get("domain", ""),
            tags=fm.get("tags", []),
            confidence=fm.get("confidence", "medium"),
            conflict_with=fm.get("conflict_with"),
            content=content,
        )
        entries.append(entry)

    return entries


def format_memory_entry(entry):
    """将 MemoryEntry 格式化为 Markdown 条目文本"""
    tags_str = ", ".join('"{}"'.format(t) for t in entry.tags)
    conflict_str = "null" if entry.conflict_with is None else entry.conflict_with

    return (
        "---\n"
        "id: {id}\n"
        "agent_id: {agent_id}\n"
        "timestamp: {timestamp}\n"
        "source_device: {source_device}\n"
        "domain: {domain}\n"
        "tags: [{tags}]\n"
        "confidence: {confidence}\n"
        "conflict_with: {conflict_with}\n"
        "---\n"
        "{content}\n"
    ).format(
        id=entry.id,
        agent_id=entry.agent_id,
        timestamp=entry.timestamp,
        source_device=entry.source_device,
        domain=entry.domain,
        tags=tags_str,
        confidence=entry.confidence,
        conflict_with=conflict_str,
        content=entry.content,
    )


def append_memory_entry(existing_content, entry):
    """将新条目追加到现有内容末尾"""
    entry_text = format_memory_entry(entry)
    if existing_content.strip():
        return existing_content.rstrip("\n") + "\n\n" + entry_text
    else:
        return entry_text


# ---------------------------------------------------------------------------
# ID 生成
# ---------------------------------------------------------------------------

_ID_PATTERN = re.compile(r"^mem_(\d{8})_(\w+)_(\d{3})$")


def generate_memory_id(source_device, memory_root, shared_root):
    """
    生成全局唯一的记忆 ID。

    格式: mem_YYYYMMDD_<source_device>_NNN

    序号扫描范围:
      扫描所有 memory_private*.md 和 memory_shared*.md 文件,
      取"今天日期 + 本 source_device"的最大序号 + 1。
      这确保同一 Agent 在多台设备上同一天不会生成相同序号,
      防止融合时产生 ID 冲突。
    """
    date_str = now_YYYYMMDD()
    prefix = "mem_{}_{}_".format(date_str, source_device)

    max_seq = 0

    # 扫描所有私有记忆文件（包括设备专属文件）
    for md_file in memory_root.glob("memory_private*.md"):
        if md_file.suffix == ".md" and not md_file.name.endswith(".lock") and not md_file.name.endswith(".tmp"):
            max_seq = max(max_seq, _scan_max_sequence(md_file, prefix))

    # 扫描所有共享记忆文件
    for md_file in memory_root.glob("memory_shared*.md"):
        if md_file.suffix == ".md" and not md_file.name.endswith(".lock") and not md_file.name.endswith(".tmp"):
            max_seq = max(max_seq, _scan_max_sequence(md_file, prefix))

    next_seq = max_seq + 1
    if next_seq > 999:
        raise MemoryIDOverflow(
            "ID 序号溢出: 今日{}设备已生成{}条记忆, 超过999上限".format(source_device, max_seq)
        )
    return "{}{:03d}".format(prefix, next_seq)


def _scan_max_sequence(md_path, prefix):
    """在 Markdown 文件中扫描匹配前缀的最大序号"""
    max_seq = 0
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return 0

    pattern = re.compile(r"^id:\s*(mem_\d{8}_\w+_\d{3})", re.MULTILINE)
    for match in pattern.finditer(text):
        full_id = match.group(1)
        if full_id.startswith(prefix):
            m = _ID_PATTERN.match(full_id)
            if m:
                seq = int(m.group(3))
                if seq > max_seq:
                    max_seq = seq

    return max_seq


# ---------------------------------------------------------------------------
# 启动流程 (第 5.1 节, 全部 8 步)
# ---------------------------------------------------------------------------

def load_identity(identity_path, device_config_path=None):
    """
    第 1~2 步: 读取 identity.json + device_config.json, 确认 agent_id 与所有路径。
    source_device 从 device_config.json 读取, 不再从 identity.json 读取。
    """
    if not identity_path.exists():
        raise IdentityNotFoundError(
            "identity.json 不存在: {}".format(identity_path)
        )

    raw = json.loads(identity_path.read_text(encoding="utf-8"))

    required_fields = [
        "agent_id", "display_name", "primary_domain",
        "memory_root", "shared_root", "created_at"
    ]
    for f in required_fields:
        if f not in raw:
            raise IdentityNotFoundError(
                "identity.json 缺少必填字段: {}".format(f)
            )

    if device_config_path is None:
        device_config_path = Path(__file__).parent / "device_config.json"

    if not device_config_path.exists():
        raise DeviceConfigNotFoundError(
            "device_config.json not found at {}. "
            'Please create it with: {{ "source_device": "<your_device_name>" }}'.format(
                device_config_path
            )
        )

    device_raw = json.loads(device_config_path.read_text(encoding="utf-8"))
    if "source_device" not in device_raw:
        raise DeviceConfigNotFoundError(
            "device_config.json 缺少 source_device 字段: {}".format(
                device_config_path
            )
        )

    return Identity(
        agent_id=raw["agent_id"],
        display_name=raw["display_name"],
        primary_domain=raw["primary_domain"],
        memory_root=Path(raw["memory_root"]),
        shared_root=Path(raw["shared_root"]),
        source_device=device_raw["source_device"],
        created_at=raw["created_at"],
    )


def load_writing_policy(shared_root):
    """第 4 步: 读取 writing_policy.md, 加载到系统提示中"""
    policy_path = shared_root / "writing_policy.md"
    if not policy_path.exists():
        raise AgentMemoryError(
            "writing_policy.md 不存在: {}".format(policy_path)
        )
    return policy_path.read_text(encoding="utf-8")


def load_runtime_manual(shared_root):
    """第 3 步: 读取 agent_runtime_manual.md, 加载为系统准则"""
    manual_path = shared_root / "agent_runtime_manual.md"
    if not manual_path.exists():
        raise AgentMemoryError(
            "agent_runtime_manual.md 不存在: {}".format(manual_path)
        )
    return manual_path.read_text(encoding="utf-8")


def recover_pending_if_exists(memory_root):
    """
    第 3 步: 检测 memory_private.md.pending 是否存在,
    若存在则将其内容并入 memory_private.md 后删除。
    """
    pending_path = memory_root / "memory_private.md.pending"
    private_path = memory_root / "memory_private.md"

    if not pending_path.exists():
        return

    pending_content = pending_path.read_text(encoding="utf-8")
    if not pending_content.strip():
        pending_path.unlink()
        return

    existing = read_file_if_exists(private_path)
    merged = existing.rstrip("\n") + "\n\n" + pending_content.strip() + "\n"

    # 原子写入: 先写临时文件, 再 rename, 防止崩溃导致重复合并
    tmp_path = private_path.with_suffix(".md.merge_tmp")
    try:
        tmp_path.write_text(merged, encoding="utf-8")
        fsync_file(tmp_path)
        os.replace(str(tmp_path), str(private_path))
        pending_path.unlink()
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def load_last_sync(memory_root):
    """第 4 步: 读取 last_sync.json"""
    sync_path = memory_root / "last_sync.json"
    if not sync_path.exists():
        raise AgentMemoryError(
            "last_sync.json 不存在: {}".format(sync_path)
        )

    raw = json.loads(sync_path.read_text(encoding="utf-8"))
    return SyncStatus(
        agent_id=raw.get("agent_id", ""),
        last_merge_timestamp=raw.get("last_merge_timestamp"),
        last_merge_id=raw.get("last_merge_id"),
        shared_memory_version=raw.get("shared_memory_version", "v0"),
    )


def should_reload_shared_memory(sync_status):
    """
    第 4 步 (辅助): 判断是否需要重新加载共享记忆。
    MVP 阶段: 若 last_merge_timestamp 为 null (从未融合过), 不需要加载。
    """
    return sync_status.last_merge_timestamp is not None


def load_private_memories(memory_root):
    """第 5 步: 加载私有记忆（扫描所有设备文件）"""
    all_memories = []

    # 扫描 memory_private_*.md 和 memory_private.md
    for md_file in memory_root.glob("memory_private*.md"):
        if md_file.suffix == ".md" and not md_file.name.endswith(".lock") and not md_file.name.endswith(".tmp"):
            text = read_file_if_exists(md_file)
            memories = parse_memories(text)
            all_memories.extend(memories)

    return all_memories


def load_shared_memories(memory_root):
    """第 5 步: 加载共享记忆"""
    shared_path = memory_root / "memory_shared.md"
    text = read_file_if_exists(shared_path)
    return parse_memories(text)


def check_conflict_files(memory_root):
    """
    第 6 步: 检查 OneDrive 冲突文件。
    OneDrive 冲突文件命名格式: memory_private-<设备名>.md
    若存在冲突文件, 返回 False (暂停写入); 否则返回 True。
    """
    conflict_pattern = re.compile(r"^memory_private-.*\.md$")
    for child in memory_root.iterdir():
        if child.is_file() and conflict_pattern.match(child.name):
            return False
    return True


def startup(identity_path=None, device_config_path=None):
    """
    启动流程 (第 5.1 节, 全部 8 步)

    Parameters
    ----------
    identity_path : Path, optional
        identity.json 的路径。若为 None, 则从模块默认路径推断。
    device_config_path : Path, optional
        device_config.json 的路径。若为 None, 则从模块目录推断。

    Returns
    -------
    StartupContext
        包含 identity, runtime_manual, policy, private_memories, shared_memories 的完整上下文。
    """
    global _context

    # 第 1~2 步: 读取 identity.json + device_config.json
    identity = load_identity(identity_path, device_config_path)

    # 第 3 步: 读取 agent_runtime_manual.md
    runtime_manual = load_runtime_manual(identity.shared_root)

    # 第 4 步: 读取 writing_policy.md
    policy = load_writing_policy(identity.shared_root)

    # 第 5 步: 检测并恢复 .pending 文件
    recover_pending_if_exists(identity.memory_root)

    # 第 6 步: 读取 last_sync.json, 判断是否需要重新加载共享记忆
    sync_status = load_last_sync(identity.memory_root)
    if should_reload_shared_memory(sync_status):
        pass

    # 第 7 步: 加载私有记忆 + 共享记忆
    private_memories = load_private_memories(identity.memory_root)
    shared_memories = load_shared_memories(identity.memory_root)

    # 第 8 步: 检查 OneDrive 冲突文件
    write_allowed = check_conflict_files(identity.memory_root)

    _context = StartupContext(
        identity=identity,
        runtime_manual=runtime_manual,
        policy=policy,
        private_memories=private_memories,
        shared_memories=shared_memories,
        write_allowed=write_allowed,
    )

    logger = get_logger()
    logger.info("启动完成: agent={}, device={}, memory_root={}".format(
        identity.agent_id, identity.source_device, identity.memory_root))
    logger.info("私有记忆: {} 条, 共享记忆: {} 条, 写入许可: {}".format(
        len(private_memories), len(shared_memories), write_allowed))

    return _context


# ---------------------------------------------------------------------------
# 写入流程 (第 5.2 节, 落盘 7 步, MVP 简化版)
# ---------------------------------------------------------------------------

def validate_content(content, tags, confidence):
    """
    步骤 1: policy_check() - 基础字段校验。
    验证写入内容是否符合 writing_policy.md 的基本约束。
    """
    if not content or not content.strip():
        raise PolicyValidationError("记忆内容不能为空")

    if not tags or len(tags) == 0:
        raise PolicyValidationError("至少需要 1 个标签")

    valid_confidence = {"high", "medium", "low"}
    if confidence not in valid_confidence:
        raise PolicyValidationError(
            "confidence 必须为 {} 之一, 实际值: {}".format(
                valid_confidence, confidence)
        )

    if re.search(r"<[a-zA-Z/][^>]*>", content):
        raise PolicyValidationError("正文不允许嵌入 HTML")


def verify_written_entry(target_file, entry):
    """
    步骤 7: 自检 - 重新解析刚写入的条目, 字段不完整则回滚。
    """
    text = target_file.read_text(encoding="utf-8")
    entries = parse_memories(text)

    found = any(e.id == entry.id for e in entries)
    if not found:
        raise VerifyError(
            "自检失败: 未找到刚写入的条目 {}".format(entry.id)
        )

    for e in entries:
        if e.id == entry.id:
            if not e.agent_id or not e.timestamp or not e.content:
                raise VerifyError(
                    "自检失败: 条目 {} 字段不完整".format(entry.id)
                )
            break


def write_memory(content, tags, confidence="high", domain=None, device_specific=True):
    """
    MVP 版写入流程 - 直接落盘, 无缓冲, 无节流, 无去重。

    落盘流程 7 步:
      1. policy_check() - 基础字段校验
      2. 生成 front matter 和 memory_id
      3. 获取文件锁 (with 上下文管理器)
      4. 写临时文件 -> fsync -> rename
      5. 删除 .pending (MVP 无缓冲, 空操作)
      6. 释放锁 (上下文管理器自动处理)
      7. 自检 - 验证刚写入的条目

    Parameters
    ----------
    content : str
        记忆正文内容。
    tags : list[str]
        标签列表, 至少 1 个。
    confidence : str
        置信度, 枚举值 "high" / "medium" / "low", 默认 "high"。
    domain : str, optional
        领域, 默认取 identity.json 的 primary_domain。
    device_specific : bool
        是否写入设备专属文件（默认 True）。
        设为 True 时写入 memory_private_<device>.md，
        设为 False 时写入 memory_private.md（兼容旧模式）。

    Returns
    -------
    str
        生成的 memory_id。
    """
    ctx = get_loaded_context()
    identity = ctx.identity
    memory_root = identity.memory_root

    if not ctx.write_allowed:
        raise AgentMemoryError(
            "写入已暂停: 检测到 OneDrive 冲突文件, 请人工处理后重启"
        )

    # 步骤 1: policy_check()
    validate_content(content, tags, confidence)

    # 步骤 1.5: 敏感信息检测
    detector = get_detector()
    sensitive_result = detector.check(content)
    if sensitive_result["blocked"]:
        raise PolicyValidationError(
            "写入被阻止: 内容包含敏感信息 {}".format(sensitive_result["matches"])
        )

    # 记录写入操作日志
    logger = get_logger()
    logger.info("写入记忆: agent={}, device={}, tags={}".format(
        identity.agent_id, identity.source_device, tags))

    # 步骤 2: 生成 front matter 和 memory_id
    memory_id = generate_memory_id(
        source_device=identity.source_device,
        memory_root=memory_root,
        shared_root=identity.shared_root,
    )

    entry = MemoryEntry(
        id=memory_id,
        agent_id=identity.agent_id,
        timestamp=now_iso8601(),
        source_device=identity.source_device,
        domain=domain or identity.primary_domain,
        tags=tags,
        confidence=confidence,
        conflict_with=None,
        content=content.strip(),
    )

    # 确定目标文件
    if device_specific:
        # 设备专属文件：memory_private_<device>.md
        target_file = memory_root / "memory_private_{}.md".format(identity.source_device)
    else:
        # 兼容旧模式：memory_private.md
        target_file = memory_root / "memory_private.md"

    # 步骤 3~6: 文件锁 + 写临时文件 -> fsync -> rename
    lock_path = target_file.with_suffix(".md.lock")
    temp_file = target_file.with_suffix(".md.tmp")

    with FileLock(lock_path):
        # 步骤 4: 写临时文件 -> fsync -> rename
        existing_content = read_file_if_exists(target_file)
        new_content = append_memory_entry(existing_content, entry)

        temp_file.write_text(new_content, encoding="utf-8")
        fsync_file(temp_file)
        os.replace(str(temp_file), str(target_file))

        # 步骤 5: 删除 .pending (MVP 无缓冲, 空操作)

        # 步骤 7: 自检
        verify_written_entry(target_file, entry)

    # 自动同步到 SQLite
    try:
        db_path = memory_root / "memories.db"
        with MemoryDatabase(db_path) as db:
            db.insert_memory(entry)
    except Exception as e:
        # SQLite 同步失败不影响写入成功
        get_logger().warning("SQLite 同步失败: {}".format(e))

    get_logger().info("记忆写入成功: id={}".format(memory_id))
    return memory_id


# ---------------------------------------------------------------------------
# SQLite 索引层 - Phase 1 新增
# ---------------------------------------------------------------------------

import sqlite3
import struct
from pathlib import Path


class MemoryDatabase:
    """SQLite 记忆索引数据库"""

    def __init__(self, db_path: Path):
        """
        初始化数据库连接

        Parameters
        ----------
        db_path : Path
            SQLite 数据库文件路径
        """
        self.db_path = db_path
        self.conn = None
        self._init_db()

    def _init_db(self):
        """初始化数据库表结构（v1.3.2：OneDrive 友好的稳健 SQLite 配置）

        关键改动：
        - timeout=60s：让 OneDrive 文件锁卡顿时不立刻 timeout
        - PRAGMA busy_timeout=60000：内部重试 60 秒
        - PRAGMA journal_mode=DELETE：不再用 WAL/SHM（OneDrive 同步时常锁 shm/wal）
        - PRAGMA synchronous=NORMAL：跳过每次 fsync，换取 OneDrive 上的稳定性
        - PRAGMA cache_size=-20000：20MB 内存缓存
        """
        import sqlite3
        # timeout=60 + busy_timeout=60000 让 OneDrive 短暂锁定时不立即崩溃
        self.conn = sqlite3.connect(str(self.db_path), timeout=60)
        self.conn.row_factory = sqlite3.Row

        # OneDrive 友好的稳健配置（必须在建表/索引前设置）
        try:
            self.conn.execute("PRAGMA busy_timeout = 60000")
            self.conn.execute("PRAGMA journal_mode = DELETE")
            self.conn.execute("PRAGMA synchronous = NORMAL")
            self.conn.execute("PRAGMA cache_size = -20000")  # 20MB
        except Exception:
            # 在某些驱动/平台上部分 PRAGMA 可能不支持，不阻塞建表
            pass

        # 创建主表
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                source_device TEXT NOT NULL,
                domain TEXT NOT NULL,
                confidence TEXT NOT NULL,
                conflict_with TEXT,
                content TEXT NOT NULL,
                embedding BLOB,
                access_count INTEGER DEFAULT 0,
                last_accessed TEXT,
                source_memory_id TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # 创建标签表
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)

        # 创建记忆-标签关联表
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_tags (
                memory_id TEXT NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (memory_id, tag_id),
                FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
        """)

        # 创建全文搜索索引 (FTS5)
        self.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                id UNINDEXED,
                content,
                domain UNINDEXED,
                tokenize='unicode61'
            )
        """)

        # 创建索引
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_agent_id ON memories(agent_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_domain ON memories(domain)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_confidence ON memories(confidence)")

        # 创建元数据表（用于数据库版本管理）
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        self.conn.commit()

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _load_tag_cache(self) -> dict:
        """加载 tag 缓存（key: tag_name, value: tag_id），加速批量插入"""
        cache = {}
        try:
            for r in self.conn.execute("SELECT id, name FROM tags").fetchall():
                cache[r["name"]] = r["id"]
        except Exception:
            pass
        return cache

    def insert_memories_batch(self, entries) -> int:
        """
        批量插入记忆条目（v1.3.2：消除 SQLite 频繁 commit 导致的 OneDrive disk I/O error）

        关键路径：
        - 一次性事务：所有 INSERT 只 commit 一次（减少 99% fsync）
        - FTS5 同事务更新，避免每条都触发倒排重建
        - 标签 ID 缓存批量复用，同一 tag 不重复查询
        """
        if not entries:
            return 0
        success = 0
        tag_cache = self._load_tag_cache()
        try:
            for entry in entries:
                try:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO memories "
                        "(id, agent_id, timestamp, source_device, domain, confidence, "
                        "conflict_with, content, embedding, access_count, last_accessed, "
                        "source_memory_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            entry.id, entry.agent_id, entry.timestamp, entry.source_device,
                            entry.domain, entry.confidence, entry.conflict_with, entry.content,
                            entry.embedding, entry.access_count, entry.last_accessed,
                            entry.source_memory_id
                        )
                    )
                    self.conn.execute(
                        "INSERT OR REPLACE INTO memories_fts (id, content, domain) VALUES (?, ?, ?)",
                        (entry.id, entry.content, entry.domain),
                    )
                    for tag in entry.tags:
                        if tag not in tag_cache:
                            self.conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
                            row = self.conn.execute(
                                "SELECT id FROM tags WHERE name = ?", (tag,)
                            ).fetchone()
                            if row:
                                tag_cache[tag] = row[0]
                        tid = tag_cache.get(tag)
                        if tid is not None:
                            self.conn.execute(
                                "INSERT OR IGNORE INTO memory_tags (memory_id, tag_id) VALUES (?, ?)",
                                (entry.id, tid),
                            )
                    success += 1
                except Exception:
                    # 单条失败不阻塞整批
                    pass
            self.conn.commit()
            return success
        except Exception as e:
            self.conn.rollback()
            raise AgentMemoryError("批量插入失败: {}".format(e))

    def insert_memory(self, entry: MemoryEntry) -> bool:
        """单条插入（兼容旧接口；大批量请用 insert_memories_batch）"""
        return self.insert_memories_batch([entry]) > 0

    def search_by_keyword(
        self,
        query: str,
        tags: list = None,
        domain: str = None,
        time_range: tuple = None,
        limit: int = 10,
        min_confidence: str = "low"
    ) -> list:
        """
        关键词搜索

        Parameters
        ----------
        query : str
            搜索关键词
        tags : list, optional
            标签过滤
        domain : str, optional
            领域过滤
        time_range : tuple, optional
            时间范围 (start_iso, end_iso)
        limit : int
            返回结果数量限制
        min_confidence : str
            最小置信度

        Returns
        -------
        list
            MemoryEntry 列表
        """
        confidence_order = {"high": 3, "medium": 2, "low": 1}
        min_conf_value = confidence_order.get(min_confidence, 1)

        # 构建查询 - 使用 LIKE 搜索支持中文
        sql = """
            SELECT m.* FROM memories m
            WHERE m.content LIKE ?
        """
        params = ["%{}%".format(query)]

        # 添加过滤条件
        if domain:
            sql += " AND m.domain = ?"
            params.append(domain)

        if time_range:
            sql += " AND m.timestamp BETWEEN ? AND ?"
            params.extend(time_range)

        # 置信度过滤
        sql += " AND CASE m.confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END >= ?"
        params.append(min_conf_value)

        # 标签过滤
        if tags:
            placeholders = ",".join(["?" for _ in tags])
            sql += """
                AND m.id IN (
                    SELECT mt.memory_id FROM memory_tags mt
                    JOIN tags t ON mt.tag_id = t.id
                    WHERE t.name IN ({})
                )
            """.format(placeholders)
            params.extend(tags)

        sql += " ORDER BY m.timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(sql, params)
        return self._rows_to_entries(cursor.fetchall())

    def search_by_vector(
        self,
        query_embedding: bytes,
        tags: list = None,
        domain: str = None,
        time_range: tuple = None,
        limit: int = 10,
        min_confidence: str = "low",
        similarity_threshold: float = 0.5
    ) -> list:
        """
        向量相似度搜索

        Parameters
        ----------
        query_embedding : bytes
            查询向量的字节表示
        tags : list, optional
            标签过滤
        domain : str, optional
            领域过滤
        time_range : tuple, optional
            时间范围
        limit : int
            返回结果数量限制
        min_confidence : str
            最小置信度
        similarity_threshold : float
            相似度阈值

        Returns
        -------
        list
            (MemoryEntry, similarity_score) 元组列表
        """
        confidence_order = {"high": 3, "medium": 2, "low": 1}
        min_conf_value = confidence_order.get(min_confidence, 1)

        # 构建查询
        sql = """
            SELECT m.* FROM memories m
            WHERE m.embedding IS NOT NULL
        """
        params = []

        # 添加过滤条件
        if domain:
            sql += " AND m.domain = ?"
            params.append(domain)

        if time_range:
            sql += " AND m.timestamp BETWEEN ? AND ?"
            params.extend(time_range)

        sql += " AND CASE m.confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END >= ?"
        params.append(min_conf_value)

        # 标签过滤
        if tags:
            placeholders = ",".join(["?" for _ in tags])
            sql += """
                AND m.id IN (
                    SELECT mt.memory_id FROM memory_tags mt
                    JOIN tags t ON mt.tag_id = t.id
                    WHERE t.name IN ({})
                )
            """.format(placeholders)
            params.extend(tags)

        cursor = self.conn.execute(sql, params)
        rows = cursor.fetchall()

        # 计算相似度并排序
        import numpy as np
        query_vec = np.frombuffer(query_embedding, dtype=np.float32)

        results = []
        for row in rows:
            if row['embedding']:
                try:
                    memory_vec = np.frombuffer(row['embedding'], dtype=np.float32)
                    # 余弦相似度
                    similarity = np.dot(query_vec, memory_vec) / (
                        np.linalg.norm(query_vec) * np.linalg.norm(memory_vec) + 1e-8
                    )
                    if similarity >= similarity_threshold:
                        entry = self._row_to_entry(row)
                        results.append((entry, float(similarity)))
                except Exception:
                    continue

        # 按相似度降序排序
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def get_memory(self, memory_id: str) -> MemoryEntry:
        """
        按ID获取记忆

        Parameters
        ----------
        memory_id : str
            记忆ID

        Returns
        -------
        MemoryEntry
            记忆条目

        Raises
        ------
        AgentMemoryError
            记忆不存在
        """
        cursor = self.conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise AgentMemoryError("记忆不存在: {}".format(memory_id))

        # 更新访问计数
        self.conn.execute("""
            UPDATE memories
            SET access_count = access_count + 1,
                last_accessed = datetime('now')
            WHERE id = ?
        """, (memory_id,))
        self.conn.commit()

        return self._row_to_entry(row)

    def list_memories(
        self,
        agent_id: str = None,
        domain: str = None,
        tags: list = None,
        limit: int = 50,
        offset: int = 0
    ) -> list:
        """
        列出记忆

        Parameters
        ----------
        agent_id : str, optional
            Agent ID 过滤
        domain : str, optional
            领域过滤
        tags : list, optional
            标签过滤
        limit : int
            返回数量限制
        offset : int
            偏移量

        Returns
        -------
        list
            MemoryEntry 列表
        """
        sql = "SELECT * FROM memories WHERE 1=1"
        params = []

        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)

        if domain:
            sql += " AND domain = ?"
            params.append(domain)

        if tags:
            placeholders = ",".join(["?" for _ in tags])
            sql += """
                AND id IN (
                    SELECT mt.memory_id FROM memory_tags mt
                    JOIN tags t ON mt.tag_id = t.id
                    WHERE t.name IN ({})
                )
            """.format(placeholders)
            params.extend(tags)

        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = self.conn.execute(sql, params)
        return self._rows_to_entries(cursor.fetchall())

    def _rows_to_entries(self, rows) -> list:
        """将数据库行转换为 MemoryEntry 列表"""
        entries = []
        for row in rows:
            entry = self._row_to_entry(row)

            # 获取标签
            cursor = self.conn.execute("""
                SELECT t.name FROM tags t
                JOIN memory_tags mt ON t.id = mt.tag_id
                WHERE mt.memory_id = ?
            """, (entry.id,))
            entry.tags = [r[0] for r in cursor.fetchall()]

            entries.append(entry)
        return entries

    def _row_to_entry(self, row) -> MemoryEntry:
        """将单个数据库行转换为 MemoryEntry"""
        return MemoryEntry(
            id=row['id'],
            agent_id=row['agent_id'],
            timestamp=row['timestamp'],
            source_device=row['source_device'],
            domain=row['domain'],
            tags=[],  # 单独获取
            confidence=row['confidence'],
            conflict_with=row['conflict_with'],
            content=row['content'],
            embedding=row['embedding'],
            access_count=row['access_count'],
            last_accessed=row['last_accessed'],
            source_memory_id=row['source_memory_id']
        )

    def sync_from_markdown(self, md_path: Path, agent_id: str) -> int:
        """
        从 Markdown 文件同步记忆到索引

        Parameters
        ----------
        md_path : Path
            Markdown 文件路径
        agent_id : str
            Agent ID

        Returns
        -------
        int
            同步的记忆数量
        """
        if not md_path.exists():
            return 0

        text = md_path.read_text(encoding="utf-8")
        entries = parse_memories(text)

        count = 0
        for entry in entries:
            # 检查是否已存在
            cursor = self.conn.execute(
                "SELECT id FROM memories WHERE id = ?", (entry.id,)
            )
            if cursor.fetchone():
                continue

            self.insert_memory(entry)
            count += 1

        return count


# ---------------------------------------------------------------------------
# Embedding 服务 - Phase 1 新增
# ---------------------------------------------------------------------------

class EmbeddingService:
    """Embedding 向量生成服务"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        初始化 Embedding 服务

        Parameters
        ----------
        model_name : str
            sentence-transformers 模型名称
        """
        self.model_name = model_name
        self.model = None
        self._dimension = None

    def _load_model(self):
        """延迟加载模型"""
        if self.model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer(self.model_name)
                # 获取向量维度
                test_embedding = self.model.encode(["test"])
                self._dimension = len(test_embedding[0])
            except ImportError:
                raise AgentMemoryError(
                    "sentence-transformers 未安装。请运行: pip install sentence-transformers"
                )

    @property
    def dimension(self) -> int:
        """获取向量维度"""
        self._load_model()
        return self._dimension

    def encode(self, texts: list) -> list:
        """
        生成文本的向量表示

        Parameters
        ----------
        texts : list
            文本列表

        Returns
        -------
        list
            向量列表 (bytes)
        """
        self._load_model()
        import numpy as np

        embeddings = self.model.encode(texts, convert_to_numpy=True)
        # 转换为 bytes
        return [emb.astype(np.float32).tobytes() for emb in embeddings]

    def encode_single(self, text: str) -> bytes:
        """
        生成单个文本的向量表示

        Parameters
        ----------
        text : str
            输入文本

        Returns
        -------
        bytes
            向量的字节表示
        """
        return self.encode([text])[0]


# ---------------------------------------------------------------------------
# 搜索 API - Phase 1 新增
# ---------------------------------------------------------------------------

def search_memory(
    query: str,
    mode: str = "hybrid",
    tags: list = None,
    domain: str = None,
    time_range: tuple = None,
    limit: int = 10,
    min_confidence: str = "low",
    db_path: Path = None,
    embedding_service: EmbeddingService = None
) -> list:
    """
    搜索记忆

    Parameters
    ----------
    query : str
        搜索查询
    mode : str
        搜索模式: "keyword" | "vector" | "hybrid"
    tags : list, optional
        标签过滤
    domain : str, optional
        领域过滤
    time_range : tuple, optional
        时间范围 (start_iso, end_iso)
    limit : int
        返回结果数量
    min_confidence : str
        最小置信度
    db_path : Path, optional
        数据库路径，默认从当前上下文获取
    embedding_service : EmbeddingService, optional
        Embedding 服务实例

    Returns
    -------
    list
        MemoryEntry 列表（向量搜索时包含相似度分数）
    """
    ctx = get_loaded_context()
    if db_path is None:
        db_path = ctx.identity.memory_root / "memories.db"

    logger = get_logger()
    logger.info("搜索记忆: query='{}', mode={}, tags={}".format(query, mode, tags))

    with MemoryDatabase(db_path) as db:
        if mode == "keyword":
            return db.search_by_keyword(
                query=query, tags=tags, domain=domain,
                time_range=time_range, limit=limit,
                min_confidence=min_confidence
            )

        elif mode == "vector":
            if embedding_service is None:
                embedding_service = EmbeddingService()
            query_embedding = embedding_service.encode_single(query)
            return db.search_by_vector(
                query_embedding=query_embedding, tags=tags, domain=domain,
                time_range=time_range, limit=limit,
                min_confidence=min_confidence
            )

        elif mode == "hybrid":
            # 混合搜索：同时执行关键词和向量搜索，合并结果
            keyword_results = db.search_by_keyword(
                query=query, tags=tags, domain=domain,
                time_range=time_range, limit=limit * 2,
                min_confidence=min_confidence
            )

            if embedding_service is None:
                embedding_service = EmbeddingService()
            query_embedding = embedding_service.encode_single(query)
            vector_results = db.search_by_vector(
                query_embedding=query_embedding, tags=tags, domain=domain,
                time_range=time_range, limit=limit * 2,
                min_confidence=min_confidence
            )

            # 合并结果，去重，按相关性排序
            seen_ids = set()
            combined = []

            # 向量搜索结果优先
            for entry, similarity in vector_results:
                if entry.id not in seen_ids:
                    seen_ids.add(entry.id)
                    combined.append((entry, similarity))

            # 添加关键词搜索结果
            for entry in keyword_results:
                if entry.id not in seen_ids:
                    seen_ids.add(entry.id)
                    combined.append((entry, 0.0))  # 关键词搜索无相似度分数

            # 按相似度排序（向量结果优先）
            combined.sort(key=lambda x: x[1], reverse=True)
            return [entry for entry, _ in combined[:limit]]

        else:
            raise AgentMemoryError("无效的搜索模式: {}".format(mode))


def get_memory(memory_id: str, db_path: Path = None) -> MemoryEntry:
    """
    按ID获取记忆

    Parameters
    ----------
    memory_id : str
        记忆ID
    db_path : Path, optional
        数据库路径

    Returns
    -------
    MemoryEntry
        记忆条目
    """
    ctx = get_loaded_context()
    if db_path is None:
        db_path = ctx.identity.memory_root / "memories.db"

    with MemoryDatabase(db_path) as db:
        return db.get_memory(memory_id)


def list_memories(
    agent_id: str = None,
    domain: str = None,
    tags: list = None,
    limit: int = 50,
    offset: int = 0,
    db_path: Path = None
) -> list:
    """
    列出记忆

    Parameters
    ----------
    agent_id : str, optional
        Agent ID 过滤
    domain : str, optional
        领域过滤
    tags : list, optional
        标签过滤
    limit : int
        返回数量
    offset : int
        偏移量
    db_path : Path, optional
        数据库路径

    Returns
    -------
    list
        MemoryEntry 列表
    """
    ctx = get_loaded_context()
    if db_path is None:
        db_path = ctx.identity.memory_root / "memories.db"

    with MemoryDatabase(db_path) as db:
        return db.list_memories(
            agent_id=agent_id, domain=domain, tags=tags,
            limit=limit, offset=offset
        )


def sync_markdown_to_db(md_path: Path = None, db_path: Path = None, sync_all_devices: bool = True) -> int:
    """
    同步 Markdown 文件到 SQLite 索引

    Parameters
    ----------
    md_path : Path, optional
        Markdown 文件路径。如果 sync_all_devices=True，此参数被忽略。
    db_path : Path, optional
        数据库路径
    sync_all_devices : bool
        是否同步所有设备的记忆（默认 True）。
        设为 True 时，会扫描 memory_private_*.md 和 memory_private.md，
        合并所有设备的记忆。

    Returns
    -------
    int
        同步的记忆数量
    """
    ctx = get_loaded_context()
    memory_root = ctx.identity.memory_root
    agent_id = ctx.identity.agent_id

    if db_path is None:
        db_path = memory_root / "memories.db"

    total_count = 0
    logger = get_logger()

    with MemoryDatabase(db_path) as db:
        if sync_all_devices:
            # 扫描所有设备的记忆文件
            # 匹配 memory_private_*.md 和 memory_private.md
            for md_file in memory_root.glob("memory_private*.md"):
                if md_file.suffix == ".md" and not md_file.name.endswith(".lock") and not md_file.name.endswith(".tmp"):
                    count = db.sync_from_markdown(md_file, agent_id)
                    total_count += count
                    if count > 0:
                        logger.info("同步文件: {} ({} 条记忆)".format(md_file.name, count))
        else:
            # 只同步指定文件或默认文件
            if md_path is None:
                md_path = memory_root / "memory_private.md"
            total_count = db.sync_from_markdown(md_path, agent_id)

    if total_count > 0:
        logger.info("同步完成: 共 {} 条记忆已索引".format(total_count))

    return total_count


def get_all_device_memories(memory_root: Path = None) -> dict:
    """
    获取所有设备的记忆文件

    Parameters
    ----------
    memory_root : Path, optional
        记忆根目录

    Returns
    -------
    dict
        {device_name: md_path} 映射
    """
    if memory_root is None:
        ctx = get_loaded_context()
        memory_root = ctx.identity.memory_root

    device_files = {}

    # 扫描 memory_private_*.md
    for md_file in memory_root.glob("memory_private_*.md"):
        if md_file.suffix == ".md" and not md_file.name.endswith(".lock") and not md_file.name.endswith(".tmp"):
            # 提取设备名：memory_private_xxx.md -> xxx
            device_name = md_file.stem.replace("memory_private_", "")
            if device_name:
                device_files[device_name] = md_file

    # 兼容旧格式：memory_private.md
    legacy_file = memory_root / "memory_private.md"
    if legacy_file.exists():
        device_files["legacy"] = legacy_file

    return device_files


# ---------------------------------------------------------------------------
# 融合器 - Phase 2 新增
# ---------------------------------------------------------------------------

class MemoryMerger:
    """跨 Agent 记忆融合器"""

    def __init__(
        self,
        shared_db_path: Path,
        agent_dbs: dict = None,
        embedding_service: EmbeddingService = None,
        similarity_threshold: float = 0.85
    ):
        """
        初始化融合器

        Parameters
        ----------
        shared_db_path : Path
            共享数据库路径
        agent_dbs : dict, optional
            Agent 数据库路径映射 {agent_id: db_path}
        embedding_service : EmbeddingService, optional
            Embedding 服务实例
        similarity_threshold : float
            相似度阈值，超过此值认为是重复记忆
        """
        self.shared_db_path = shared_db_path
        self.agent_dbs = agent_dbs or {}
        self.embedding_service = embedding_service
        self.similarity_threshold = similarity_threshold

    def register_agent(self, agent_id: str, db_path: Path):
        """
        注册 Agent 数据库

        Parameters
        ----------
        agent_id : str
            Agent ID
        db_path : Path
            数据库路径
        """
        self.agent_dbs[agent_id] = db_path

    def sync_agent_to_shared(self, agent_id: str) -> dict:
        """
        将 Agent 的记忆同步到共享数据库

        Parameters
        ----------
        agent_id : str
            Agent ID

        Returns
        -------
        dict
            同步结果统计
        """
        if agent_id not in self.agent_dbs:
            raise AgentMemoryError("未注册的 Agent: {}".format(agent_id))

        agent_db_path = self.agent_dbs[agent_id]
        stats = {"synced": 0, "skipped": 0, "conflicts": 0}

        with MemoryDatabase(agent_db_path) as agent_db, \
             MemoryDatabase(self.shared_db_path) as shared_db:

            # 获取 Agent 的所有记忆
            agent_memories = agent_db.list_memories(limit=10000)

            for memory in agent_memories:
                # 设置来源
                memory.source_memory_id = memory.id

                # 检查是否已存在于共享库
                existing = self._find_similar_in_shared(shared_db, memory)

                if existing:
                    # 存在相似记忆，处理冲突
                    conflict_result = self._resolve_conflict(existing, memory)
                    if conflict_result == "keep_existing":
                        stats["skipped"] += 1
                    elif conflict_result == "replace":
                        self._replace_in_shared(shared_db, existing.id, memory)
                        stats["synced"] += 1
                    elif conflict_result == "merge":
                        merged = self._merge_memories(existing, memory)
                        self._replace_in_shared(shared_db, existing.id, merged)
                        stats["synced"] += 1
                    stats["conflicts"] += 1
                else:
                    # 无冲突，直接插入
                    shared_db.insert_memory(memory)
                    stats["synced"] += 1

        return stats

    def sync_shared_to_agent(self, agent_id: str) -> dict:
        """
        将共享记忆同步到 Agent

        Parameters
        ----------
        agent_id : str
            Agent ID

        Returns
        -------
        dict
            同步结果统计
        """
        if agent_id not in self.agent_dbs:
            raise AgentMemoryError("未注册的 Agent: {}".format(agent_id))

        agent_db_path = self.agent_dbs[agent_id]
        stats = {"synced": 0, "skipped": 0}

        with MemoryDatabase(self.shared_db_path) as shared_db, \
             MemoryDatabase(agent_db_path) as agent_db:

            # 获取共享库中非本 Agent 的记忆
            shared_memories = shared_db.list_memories(limit=10000)

            for memory in shared_memories:
                # 跳过自己创建的记忆
                if memory.agent_id == agent_id:
                    continue

                # 检查是否已存在
                cursor = agent_db.conn.execute(
                    "SELECT id FROM memories WHERE id = ?", (memory.id,)
                )
                if cursor.fetchone():
                    stats["skipped"] += 1
                    continue

                # 插入到 Agent 数据库
                agent_db.insert_memory(memory)
                stats["synced"] += 1

        return stats

    def full_sync(self) -> dict:
        """
        执行完整同步：所有 Agent -> 共享库 -> 所有 Agent

        Returns
        -------
        dict
            所有 Agent 的同步结果
        """
        logger = get_logger()
        logger.info("开始完整融合: agents={}".format(list(self.agent_dbs.keys())))
        results = {}

        # 第一阶段：所有 Agent -> 共享库
        for agent_id in self.agent_dbs:
            result = self.sync_agent_to_shared(agent_id)
            results["{}_to_shared".format(agent_id)] = result
            logger.info("{} -> 共享库: synced={}, skipped={}, conflicts={}".format(
                agent_id, result["synced"], result["skipped"], result["conflicts"]))

        # 第二阶段：共享库 -> 所有 Agent
        for agent_id in self.agent_dbs:
            result = self.sync_shared_to_agent(agent_id)
            results["shared_to_{}".format(agent_id)] = result
            logger.info("共享库 -> {}: synced={}, skipped={}".format(
                agent_id, result["synced"], result["skipped"]))

        logger.info("完整融合完成")
        return results

    def _find_similar_in_shared(self, shared_db: MemoryDatabase, memory: MemoryEntry) -> MemoryEntry:
        """
        在共享库中查找相似记忆

        Parameters
        ----------
        shared_db : MemoryDatabase
            共享数据库
        memory : MemoryEntry
            要查找的记忆

        Returns
        -------
        MemoryEntry or None
            相似记忆，不存在返回 None
        """
        # 首先精确匹配 ID
        cursor = shared_db.conn.execute(
            "SELECT id FROM memories WHERE id = ?", (memory.id,)
        )
        if cursor.fetchone():
            return shared_db.get_memory(memory.id)

        # 使用向量相似度搜索（如果有 embedding）
        if memory.embedding and self.embedding_service:
            results = shared_db.search_by_vector(
                query_embedding=memory.embedding,
                limit=1,
                similarity_threshold=self.similarity_threshold
            )
            if results:
                return results[0][0]  # 返回最相似的记忆

        # 降级到内容相似度检查
        cursor = shared_db.conn.execute(
            "SELECT id FROM memories WHERE content = ?", (memory.content,)
        )
        row = cursor.fetchone()
        if row:
            return shared_db.get_memory(row['id'])

        return None

    def _resolve_conflict(self, existing: MemoryEntry, new: MemoryEntry) -> str:
        """
        解决冲突

        Parameters
        ----------
        existing : MemoryEntry
            已存在的记忆
        new : MemoryEntry
            新记忆

        Returns
        -------
        str
            解决策略: "keep_existing" | "replace" | "merge"
        """
        # 置信度比较
        confidence_order = {"high": 3, "medium": 2, "low": 1}
        existing_conf = confidence_order.get(existing.confidence, 1)
        new_conf = confidence_order.get(new.confidence, 1)

        if new_conf > existing_conf:
            return "replace"
        elif new_conf < existing_conf:
            return "keep_existing"

        # 置信度相同，比较时间
        if new.timestamp > existing.timestamp:
            return "replace"

        # 访问频率比较
        if new.access_count > existing.access_count:
            return "replace"

        return "keep_existing"

    def _merge_memories(self, existing: MemoryEntry, new: MemoryEntry) -> MemoryEntry:
        """
        合并两条记忆

        Parameters
        ----------
        existing : MemoryEntry
            已存在的记忆
        new : MemoryEntry
            新记忆

        Returns
        -------
        MemoryEntry
            合并后的记忆
        """
        # 合并标签
        merged_tags = list(set(existing.tags + new.tags))

        # 选择更高的置信度
        confidence_order = {"high": 3, "medium": 2, "low": 1}
        if confidence_order.get(new.confidence, 1) > confidence_order.get(existing.confidence, 1):
            confidence = new.confidence
        else:
            confidence = existing.confidence

        # 合并内容（保留更详细的版本）
        if len(new.content) > len(existing.content):
            content = new.content
        else:
            content = existing.content

        # 创建合并后的记忆
        merged = MemoryEntry(
            id=existing.id,
            agent_id=existing.agent_id,
            timestamp=max(existing.timestamp, new.timestamp),
            source_device=existing.source_device,
            domain=existing.domain,
            tags=merged_tags,
            confidence=confidence,
            conflict_with=None,
            content=content,
            embedding=existing.embedding or new.embedding,
            access_count=max(existing.access_count, new.access_count),
            last_accessed=max(
                existing.last_accessed or "",
                new.last_accessed or ""
            ) or None,
            source_memory_id=existing.source_memory_id
        )

        return merged

    def _replace_in_shared(self, shared_db: MemoryDatabase, old_id: str, new_memory: MemoryEntry):
        """
        替换共享库中的记忆

        Parameters
        ----------
        shared_db : MemoryDatabase
            共享数据库
        old_id : str
            旧记忆 ID
        new_memory : MemoryEntry
            新记忆
        """
        # 删除旧记忆
        shared_db.conn.execute("DELETE FROM memories WHERE id = ?", (old_id,))
        shared_db.conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (old_id,))

        # 插入新记忆
        shared_db.insert_memory(new_memory)


# ---------------------------------------------------------------------------
# 去重服务 - Phase 3 新增
# ---------------------------------------------------------------------------

class DeduplicationService:
    """记忆去重服务"""

    def __init__(
        self,
        db_path: Path,
        embedding_service: EmbeddingService = None,
        similarity_threshold: float = 0.95
    ):
        """
        初始化去重服务

        Parameters
        ----------
        db_path : Path
            数据库路径
        embedding_service : EmbeddingService, optional
            Embedding 服务实例
        similarity_threshold : float
            相似度阈值，超过此值认为是重复
        """
        self.db_path = db_path
        self.embedding_service = embedding_service
        self.similarity_threshold = similarity_threshold

    def check_duplicate(self, memory: MemoryEntry) -> tuple:
        """
        检查记忆是否重复

        Parameters
        ----------
        memory : MemoryEntry
            要检查的记忆

        Returns
        -------
        tuple
            (is_duplicate: bool, similar_memory: MemoryEntry or None, similarity: float)
        """
        with MemoryDatabase(self.db_path) as db:
            # 首先检查内容完全相同
            cursor = db.conn.execute(
                "SELECT id FROM memories WHERE content = ?", (memory.content,)
            )
            row = cursor.fetchone()
            if row:
                existing = db.get_memory(row['id'])
                return True, existing, 1.0

            # 使用向量相似度检查
            if memory.embedding and self.embedding_service:
                results = db.search_by_vector(
                    query_embedding=memory.embedding,
                    limit=1,
                    similarity_threshold=self.similarity_threshold
                )
                if results:
                    similar, similarity = results[0]
                    return True, similar, similarity

            # 降级到文本相似度检查
            if self.embedding_service:
                query_embedding = self.embedding_service.encode_single(memory.content)
                results = db.search_by_vector(
                    query_embedding=query_embedding,
                    limit=1,
                    similarity_threshold=self.similarity_threshold
                )
                if results:
                    similar, similarity = results[0]
                    return True, similar, similarity

            return False, None, 0.0

    def deduplicate(self, dry_run: bool = False) -> dict:
        """
        执行去重

        Parameters
        ----------
        dry_run : bool
            如果为 True，只检测不删除

        Returns
        -------
        dict
            去重结果统计
        """
        stats = {"total": 0, "duplicates": 0, "removed": 0, "kept": 0}

        with MemoryDatabase(self.db_path) as db:
            # 获取所有记忆
            all_memories = db.list_memories(limit=10000)
            stats["total"] = len(all_memories)

            # 按时间排序，保留最新的
            all_memories.sort(key=lambda m: m.timestamp, reverse=True)

            seen_contents = set()
            seen_embeddings = []
            to_remove = []

            for memory in all_memories:
                # 检查内容完全相同
                if memory.content in seen_contents:
                    to_remove.append(memory.id)
                    stats["duplicates"] += 1
                    continue

                # 检查向量相似度
                if memory.embedding and self.embedding_service:
                    import numpy as np
                    memory_vec = np.frombuffer(memory.embedding, dtype=np.float32)

                    is_duplicate = False
                    for seen_emb in seen_embeddings:
                        seen_vec = np.frombuffer(seen_emb, dtype=np.float32)
                        similarity = np.dot(memory_vec, seen_vec) / (
                            np.linalg.norm(memory_vec) * np.linalg.norm(seen_vec) + 1e-8
                        )
                        if similarity >= self.similarity_threshold:
                            is_duplicate = True
                            break

                    if is_duplicate:
                        to_remove.append(memory.id)
                        stats["duplicates"] += 1
                        continue

                    seen_embeddings.append(memory.embedding)

                seen_contents.add(memory.content)

            # 执行删除
            if not dry_run:
                for memory_id in to_remove:
                    db.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                    db.conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (memory_id,))
                db.conn.commit()
                stats["removed"] = len(to_remove)
            else:
                stats["removed"] = 0

            stats["kept"] = stats["total"] - stats["duplicates"]

        return stats


# ---------------------------------------------------------------------------
# 记忆衰减服务 - Phase 3 新增
# ---------------------------------------------------------------------------

class MemoryDecayService:
    """记忆衰减服务"""

    def __init__(
        self,
        db_path: Path,
        decay_rate: float = 0.1,
        min_weight: float = 0.1,
        max_age_days: int = 365
    ):
        """
        初始化衰减服务

        Parameters
        ----------
        db_path : Path
            数据库路径
        decay_rate : float
            衰减率 (0-1)
        min_weight : float
            最小权重
        max_age_days : int
            最大天数（超过此天数权重为最小值）
        """
        self.db_path = db_path
        self.decay_rate = decay_rate
        self.min_weight = min_weight
        self.max_age_days = max_age_days

    def calculate_weight(self, memory: MemoryEntry) -> float:
        """
        计算记忆权重

        Parameters
        ----------
        memory : MemoryEntry
            记忆条目

        Returns
        -------
        float
            权重 (0-1)
        """
        from datetime import datetime, timezone

        # 解析时间
        try:
            if memory.last_accessed:
                last_access = datetime.fromisoformat(memory.last_accessed.replace('Z', '+00:00'))
            else:
                last_access = datetime.fromisoformat(memory.timestamp.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return 1.0

        # 计算天数
        now = datetime.now(timezone.utc)
        days_since_access = (now - last_access).days

        # 时间衰减
        time_weight = max(
            self.min_weight,
            1.0 - (days_since_access / self.max_age_days)
        )

        # 访问频率加成
        access_bonus = min(0.5, memory.access_count * 0.05)

        # 置信度加成
        confidence_bonus = {"high": 0.2, "medium": 0.1, "low": 0.0}
        conf_bonus = confidence_bonus.get(memory.confidence, 0.0)

        # 最终权重
        weight = min(1.0, time_weight + access_bonus + conf_bonus)

        return max(self.min_weight, weight)

    def update_weights(self) -> dict:
        """
        更新所有记忆的权重

        Returns
        -------
        dict
            更新结果统计
        """
        stats = {"total": 0, "updated": 0, "average_weight": 0.0}

        with MemoryDatabase(self.db_path) as db:
            all_memories = db.list_memories(limit=10000)
            stats["total"] = len(all_memories)

            total_weight = 0.0
            for memory in all_memories:
                weight = self.calculate_weight(memory)
                total_weight += weight

                # 这里可以将权重存储到数据库的某个字段
                # 目前只计算，不存储

            if stats["total"] > 0:
                stats["average_weight"] = total_weight / stats["total"]

        return stats

    def get_weighted_memories(self, limit: int = 100) -> list:
        """
        获取按权重排序的记忆

        Parameters
        ----------
        limit : int
            返回数量

        Returns
        -------
        list
            (MemoryEntry, weight) 元组列表
        """
        with MemoryDatabase(self.db_path) as db:
            all_memories = db.list_memories(limit=10000)

            # 计算权重
            weighted = []
            for memory in all_memories:
                weight = self.calculate_weight(memory)
                weighted.append((memory, weight))

            # 按权重排序
            weighted.sort(key=lambda x: x[1], reverse=True)

            return weighted[:limit]


# ---------------------------------------------------------------------------
# 便捷函数 - Phase 2 & 3 新增
# ---------------------------------------------------------------------------

def create_merger(
    shared_db_path: Path,
    agent_configs: dict = None,
    similarity_threshold: float = 0.85
) -> MemoryMerger:
    """
    创建融合器实例

    Parameters
    ----------
    shared_db_path : Path
        共享数据库路径
    agent_configs : dict, optional
        Agent 配置 {agent_id: db_path}
    similarity_threshold : float
        相似度阈值

    Returns
    -------
    MemoryMerger
        融合器实例
    """
    merger = MemoryMerger(
        shared_db_path=shared_db_path,
        similarity_threshold=similarity_threshold
    )

    if agent_configs:
        for agent_id, db_path in agent_configs.items():
            merger.register_agent(agent_id, db_path)

    return merger


def run_deduplication(db_path: Path, dry_run: bool = False) -> dict:
    """
    运行去重

    Parameters
    ----------
    db_path : Path
        数据库路径
    dry_run : bool
        是否为试运行

    Returns
    -------
    dict
        去重结果
    """
    service = DeduplicationService(db_path=db_path)
    return service.deduplicate(dry_run=dry_run)


def run_decay_update(db_path: Path) -> dict:
    """
    运行衰减更新

    Parameters
    ----------
    db_path : Path
        数据库路径

    Returns
    -------
    dict
        更新结果
    """
    service = MemoryDecayService(db_path=db_path)
    return service.update_weights()


# ---------------------------------------------------------------------------
# Trae 记忆适配器 - 新增
# ---------------------------------------------------------------------------

class TraeMemoryAdapter:
    """Trae Solo 记忆文件适配器"""

    def __init__(self, trae_memory_dir: Path):
        """
        初始化 Trae 适配器

        Parameters
        ----------
        trae_memory_dir : Path
            Trae 记忆目录 (如 ~/.trae-cn/memory)
        """
        self.trae_dir = trae_memory_dir

    def import_user_profile(self, agent_id: str, device_name: str) -> list:
        """
        导入用户偏好记忆

        Parameters
        ----------
        agent_id : str
            Agent ID
        device_name : str
            设备名称

        Returns
        -------
        list
            MemoryEntry 列表
        """
        profile_path = self.trae_dir / "user_profile.md"
        if not profile_path.exists():
            return []

        content = profile_path.read_text(encoding="utf-8")
        entries = []

        # 解析 Trae 的 user_profile.md 格式
        # 格式: ## 类别\n- 内容\n- 内容
        current_category = ""
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("## "):
                current_category = line[3:].strip()
            elif line.startswith("- "):
                memory_content = line[2:].strip()
                entry = MemoryEntry(
                    id="trae_{}_{}".format(agent_id, len(entries)),
                    agent_id=agent_id,
                    timestamp=now_iso8601(),
                    source_device=device_name,
                    domain="user_preference",
                    tags=[current_category, "trae_import"],
                    confidence="high",
                    conflict_with=None,
                    content="[{}] {}".format(current_category, memory_content)
                )
                entries.append(entry)

        return entries

    def import_project_memories(self, agent_id: str, device_name: str) -> list:
        """
        导入项目记忆

        Parameters
        ----------
        agent_id : str
            Agent ID
        device_name : str
            设备名称

        Returns
        -------
        list
            MemoryEntry 列表
        """
        entries = []
        projects_dir = self.trae_dir / "projects"

        if not projects_dir.exists():
            return []

        # 遍历所有项目目录
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue

            # 读取 project_memory.md
            memory_file = project_dir / "project_memory.md"
            if memory_file.exists():
                content = memory_file.read_text(encoding="utf-8")
                project_entries = self._parse_project_memory(
                    content, agent_id, device_name, project_dir.name
                )
                entries.extend(project_entries)

            # 读取 topics.md（按日期目录）
            for date_dir in project_dir.iterdir():
                if date_dir.is_dir() and date_dir.name.isdigit():
                    topics_file = date_dir / "topics.md"
                    if topics_file.exists():
                        topics_content = topics_file.read_text(encoding="utf-8")
                        topic_entries = self._parse_topics(
                            topics_content, agent_id, device_name, date_dir.name
                        )
                        entries.extend(topic_entries)

        return entries

    def _parse_project_memory(self, content: str, agent_id: str, device_name: str, project_id: str) -> list:
        """解析 project_memory.md"""
        entries = []
        current_section = ""

        for line in content.splitlines():
            line = line.strip()
            if line.startswith("## "):
                current_section = line[3:].strip()
            elif line.startswith("- ") and current_section:
                memory_content = line[2:].strip()
                entry = MemoryEntry(
                    id="trae_proj_{}_{}".format(project_id, len(entries)),
                    agent_id=agent_id,
                    timestamp=now_iso8601(),
                    source_device=device_name,
                    domain="project",
                    tags=[current_section, "trae_project"],
                    confidence="high",
                    conflict_with=None,
                    content="[{}] {}".format(current_section, memory_content)
                )
                entries.append(entry)

        return entries

    def _parse_topics(self, content: str, agent_id: str, device_name: str, date_str: str) -> list:
        """解析 topics.md"""
        entries = []

        # Trae topics 格式: [session_id: xxx | topic_summary_time: xxx]内容
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("[session_id:"):
                # 提取时间和内容
                bracket_end = line.index("]")
                metadata = line[1:bracket_end]
                topic_content = line[bracket_end + 1:].strip()

                # 提取时间
                time_match = re.search(r'topic_summary_time:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', metadata)
                timestamp = time_match.group(1) if time_match else now_iso8601()

                entry = MemoryEntry(
                    id="trae_topic_{}_{}".format(date_str, len(entries)),
                    agent_id=agent_id,
                    timestamp=timestamp.replace(" ", "T") + "Z",
                    source_device=device_name,
                    domain="topic",
                    tags=["trae_topic", date_str],
                    confidence="medium",
                    conflict_with=None,
                    content=topic_content
                )
                entries.append(entry)

        return entries

    def import_all(self, agent_id: str, device_name: str) -> list:
        """
        导入所有 Trae 记忆

        Parameters
        ----------
        agent_id : str
            Agent ID
        device_name : str
            设备名称

        Returns
        -------
        list
            所有 MemoryEntry
        """
        entries = []
        entries.extend(self.import_user_profile(agent_id, device_name))
        entries.extend(self.import_project_memories(agent_id, device_name))
        return entries

    def import_to_database(self, agent_id: str, device_name: str, db_path: Path) -> int:
        """
        导入 Trae 记忆到数据库

        Parameters
        ----------
        agent_id : str
            Agent ID
        device_name : str
            设备名称
        db_path : Path
            数据库路径

        Returns
        -------
        int
            导入的记忆数量
        """
        entries = self.import_all(agent_id, device_name)

        with MemoryDatabase(db_path) as db:
            count = 0
            for entry in entries:
                # 检查是否已存在
                cursor = db.conn.execute(
                    "SELECT id FROM memories WHERE id = ?", (entry.id,)
                )
                if cursor.fetchone():
                    continue

                db.insert_memory(entry)
                count += 1

        return count


def import_trae_memories(
    trae_memory_dir: Path = None,
    agent_id: str = "trae",
    device_name: str = "unknown",
    db_path: Path = None
) -> int:
    """
    导入 Trae 记忆

    Parameters
    ----------
    trae_memory_dir : Path, optional
        Trae 记忆目录，默认 ~/.trae-cn/memory
    agent_id : str
        Agent ID
    device_name : str
        设备名称
    db_path : Path, optional
        数据库路径

    Returns
    -------
    int
        导入的记忆数量
    """
    if trae_memory_dir is None:
        trae_memory_dir = Path.home() / ".trae-cn" / "memory"

    if db_path is None:
        ctx = get_loaded_context()
        db_path = ctx.identity.memory_root / "memories.db"

    adapter = TraeMemoryAdapter(trae_memory_dir)
    return adapter.import_to_database(agent_id, device_name, db_path)


# ---------------------------------------------------------------------------
# 并发写入控制 - 新增
# ---------------------------------------------------------------------------

class ConcurrentWriteManager:
    """跨机器并发写入管理器"""

    def __init__(self, db_path: Path, lock_timeout: int = None):
        """
        初始化并发写入管理器

        Parameters
        ----------
        db_path : Path
            数据库路径
        lock_timeout : int, optional
            锁超时时间（秒），默认从配置读取
        """
        config = get_config()
        self.db_path = db_path
        self.lock_timeout = lock_timeout or config.get("sync.lock_timeout_seconds", 30)
        lock_dir_name = config.get("paths.lock_dir", ".locks")
        self.lock_dir = db_path.parent / lock_dir_name
        self.lock_dir.mkdir(exist_ok=True)

    def _get_lock_path(self, operation: str) -> Path:
        """获取锁文件路径"""
        return self.lock_dir / "{}.lock".format(operation)

    def _acquire_lock(self, operation: str) -> bool:
        """
        获取锁

        Parameters
        ----------
        operation : str
            操作名称

        Returns
        -------
        bool
            是否成功获取锁
        """
        lock_path = self._get_lock_path(operation)

        # 检查现有锁
        if lock_path.exists():
            try:
                lock_time_str = lock_path.read_text(encoding="utf-8").strip()
                lock_time = datetime.fromisoformat(lock_time_str)
                if datetime.now() - lock_time > timedelta(seconds=self.lock_timeout):
                    # 锁超时，删除
                    lock_path.unlink()
                else:
                    return False
            except (ValueError, OSError):
                try:
                    lock_path.unlink()
                except:
                    pass

        # 创建锁
        try:
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(lock_fd, datetime.now().isoformat().encode("utf-8"))
            os.close(lock_fd)
            return True
        except FileExistsError:
            return False

    def _release_lock(self, operation: str):
        """释放锁"""
        lock_path = self._get_lock_path(operation)
        try:
            if lock_path.exists():
                lock_path.unlink()
        except:
            pass

    @contextmanager
    def write_lock(self, operation: str):
        """
        写入锁上下文管理器

        Parameters
        ----------
        operation : str
            操作名称
        """
        if not self._acquire_lock(operation):
            raise LockError("无法获取写入锁: {} (可能有其他设备正在写入)".format(operation))

        try:
            yield
        finally:
            self._release_lock(operation)

    def safe_write(self, operation: str, write_func, max_retries: int = None, retry_delay: float = None):
        """
        安全写入（带重试）

        Parameters
        ----------
        operation : str
            操作名称
        write_func : callable
            写入函数
        max_retries : int, optional
            最大重试次数，默认从配置读取
        retry_delay : float, optional
            重试延迟（秒），默认从配置读取

        Returns
        -------
        any
            写入函数的返回值
        """
        import time
        config = get_config()

        if max_retries is None:
            max_retries = config.get("sync.retry_count", 3)
        if retry_delay is None:
            retry_delay = config.get("sync.retry_delay_seconds", 1)

        logger = get_logger()

        for attempt in range(max_retries):
            try:
                with self.write_lock(operation):
                    return write_func()
            except LockError:
                if attempt < max_retries - 1:
                    logger.warning("写入锁获取失败，重试 {}/{} (延迟{}秒)".format(
                        attempt + 1, max_retries, retry_delay * (attempt + 1)))
                    time.sleep(retry_delay * (attempt + 1))
                else:
                    logger.error("写入锁获取失败，已达到最大重试次数")
                    raise

    def check_pending_writes(self) -> list:
        """
        检查待处理的写入

        Returns
        -------
        list
            待处理的操作列表
        """
        pending = []
        if self.lock_dir.exists():
            for lock_file in self.lock_dir.glob("*.lock"):
                try:
                    lock_time_str = lock_file.read_text(encoding="utf-8").strip()
                    lock_time = datetime.fromisoformat(lock_time_str)
                    age = (datetime.now() - lock_time).total_seconds()
                    if age < self.lock_timeout:
                        pending.append({
                            "operation": lock_file.stem,
                            "age_seconds": age,
                            "lock_file": str(lock_file)
                        })
                except:
                    pass
        return pending

    def force_release_all(self):
        """强制释放所有锁（危险操作）"""
        if self.lock_dir.exists():
            for lock_file in self.lock_dir.glob("*.lock"):
                try:
                    lock_file.unlink()
                except:
                    pass


def safe_write_memory(
    content: str,
    tags: list,
    confidence: str = "high",
    domain: str = None,
    max_retries: int = 3
) -> str:
    """
    安全写入记忆（带并发控制）

    Parameters
    ----------
    content : str
        记忆内容
    tags : list
        标签列表
    confidence : str
        置信度
    domain : str, optional
        领域
    max_retries : int
        最大重试次数

    Returns
    -------
    str
        记忆 ID
    """
    ctx = get_loaded_context()
    db_path = ctx.identity.memory_root / "memories.db"

    manager = ConcurrentWriteManager(db_path)

    def do_write():
        # 写入 Markdown
        memory_id = write_memory(content, tags, confidence, domain)
        # 同步到 SQLite
        sync_markdown_to_db()
        return memory_id

    return manager.safe_write("write_memory", do_write, max_retries=max_retries)


# ---------------------------------------------------------------------------
# 数据保护与备份 - 新增
# ---------------------------------------------------------------------------

class DataProtection:
    """数据保护服务"""

    def __init__(self, memory_root: Path, max_backups: int = None):
        """
        初始化数据保护服务

        Parameters
        ----------
        memory_root : Path
            记忆根目录
        max_backups : int, optional
            最大备份数量，默认从配置读取
        """
        config = get_config()
        self.memory_root = memory_root
        self.max_backups = max_backups or config.get("limits.max_backups", 10)
        backup_dir_name = config.get("paths.backup_dir", ".backups")
        self.backup_dir = memory_root / backup_dir_name
        self.backup_dir.mkdir(exist_ok=True)

    def backup_before_write(self, file_path: Path) -> Path:
        """
        写入前备份

        Parameters
        ----------
        file_path : Path
            要备份的文件

        Returns
        -------
        Path
            备份文件路径
        """
        if not file_path.exists():
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = "{}.{}.bak".format(file_path.stem, timestamp)
        backup_path = self.backup_dir / backup_name

        # 复制文件
        import shutil
        shutil.copy2(str(file_path), str(backup_path))

        # 清理旧备份
        self._cleanup_old_backups(file_path.stem)

        return backup_path

    def _cleanup_old_backups(self, file_stem: str):
        """清理旧备份"""
        backups = sorted(
            self.backup_dir.glob("{}.2*.bak".format(file_stem)),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        # 保留最新的 max_backups 个
        for old_backup in backups[self.max_backups:]:
            try:
                old_backup.unlink()
            except:
                pass

    def restore_backup(self, file_stem: str, backup_index: int = 0) -> bool:
        """
        恢复备份

        Parameters
        ----------
        file_stem : str
            文件名（不含扩展名）
        backup_index : int
            备份索引（0=最新，1=次新，...）

        Returns
        -------
        bool
            是否恢复成功
        """
        backups = sorted(
            self.backup_dir.glob("{}.2*.bak".format(file_stem)),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        if backup_index >= len(backups):
            return False

        backup_path = backups[backup_index]
        target_path = self.memory_root / "{}.md".format(file_stem)

        import shutil
        shutil.copy2(str(backup_path), str(target_path))

        return True

    def list_backups(self) -> dict:
        """
        列出所有备份

        Returns
        -------
        dict
            {file_stem: [backup_info, ...]}
        """
        result = {}
        for backup_file in sorted(self.backup_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime, reverse=True):
            parts = backup_file.stem.split(".", 1)
            if len(parts) == 2:
                file_stem, timestamp = parts
                if file_stem not in result:
                    result[file_stem] = []
                result[file_stem].append({
                    "path": backup_file,
                    "timestamp": timestamp,
                    "size": backup_file.stat().st_size
                })
        return result


# ---------------------------------------------------------------------------
# 数据完整性检查 - 新增
# ---------------------------------------------------------------------------

class IntegrityChecker:
    """数据完整性检查器"""

    def __init__(self, memory_root: Path):
        """
        初始化完整性检查器

        Parameters
        ----------
        memory_root : Path
            记忆根目录
        """
        self.memory_root = memory_root

    def check_markdown_integrity(self, md_path: Path) -> dict:
        """
        检查 Markdown 文件完整性

        Parameters
        ----------
        md_path : Path
            Markdown 文件路径

        Returns
        -------
        dict
            检查结果
        """
        result = {
            "file": str(md_path),
            "exists": md_path.exists(),
            "readable": False,
            "valid_entries": 0,
            "invalid_entries": 0,
            "errors": []
        }

        if not md_path.exists():
            result["errors"].append("文件不存在")
            return result

        try:
            text = md_path.read_text(encoding="utf-8")
            result["readable"] = True
        except Exception as e:
            result["errors"].append("无法读取: {}".format(e))
            return result

        entries = parse_memories(text)
        for entry in entries:
            if self._validate_entry(entry):
                result["valid_entries"] += 1
            else:
                result["invalid_entries"] += 1
                result["errors"].append("无效条目: {}".format(entry.id))

        return result

    def _validate_entry(self, entry: MemoryEntry) -> bool:
        """验证单个记忆条目"""
        if not entry.id:
            return False
        if not entry.agent_id:
            return False
        if not entry.timestamp:
            return False
        if not entry.content:
            return False
        return True

    def check_sqlite_integrity(self, db_path: Path) -> dict:
        """
        检查 SQLite 数据库完整性

        Parameters
        ----------
        db_path : Path
            数据库路径

        Returns
        -------
        dict
            检查结果
        """
        result = {
            "file": str(db_path),
            "exists": db_path.exists(),
            "valid": False,
            "total_memories": 0,
            "errors": []
        }

        if not db_path.exists():
            result["errors"].append("数据库不存在")
            return result

        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))

            # 检查完整性
            cursor = conn.execute("PRAGMA integrity_check")
            integrity = cursor.fetchone()[0]
            if integrity != "ok":
                result["errors"].append("完整性检查失败: {}".format(integrity))
            else:
                result["valid"] = True

            # 统计记忆数量
            cursor = conn.execute("SELECT COUNT(*) FROM memories")
            result["total_memories"] = cursor.fetchone()[0]

            conn.close()
        except Exception as e:
            result["errors"].append("数据库错误: {}".format(e))

        return result

    def check_all(self) -> dict:
        """
        检查所有数据完整性

        Returns
        -------
        dict
            检查结果
        """
        results = {
            "markdown_files": [],
            "sqlite_databases": [],
            "summary": {"total_files": 0, "total_errors": 0}
        }

        # 检查所有 Markdown 文件
        for md_file in self.memory_root.glob("memory_private*.md"):
            if md_file.suffix == ".md" and not md_file.name.endswith(".lock") and not md_file.name.endswith(".tmp"):
                result = self.check_markdown_integrity(md_file)
                results["markdown_files"].append(result)
                results["summary"]["total_files"] += 1
                results["summary"]["total_errors"] += len(result["errors"])

        # 检查所有 SQLite 数据库
        for db_file in self.memory_root.glob("*.db"):
            result = self.check_sqlite_integrity(db_file)
            results["sqlite_databases"].append(result)
            results["summary"]["total_files"] += 1
            results["summary"]["total_errors"] += len(result["errors"])

        return results


# ---------------------------------------------------------------------------
# 记忆优化 - 新增
# ---------------------------------------------------------------------------

class MemoryOptimizer:
    """记忆优化器"""

    def __init__(self, db_path: Path, embedding_service: EmbeddingService = None):
        """
        初始化记忆优化器

        Parameters
        ----------
        db_path : Path
            数据库路径
        embedding_service : EmbeddingService, optional
            Embedding 服务实例
        """
        self.db_path = db_path
        self.embedding_service = embedding_service

    def compress_memories(self, dry_run: bool = False) -> dict:
        """
        压缩记忆（合并相似内容）

        Parameters
        ----------
        dry_run : bool
            是否为试运行

        Returns
        -------
        dict
            压缩结果
        """
        stats = {"total": 0, "merged": 0, "removed": 0}

        with MemoryDatabase(self.db_path) as db:
            all_memories = db.list_memories(limit=10000)
            stats["total"] = len(all_memories)

            # 按内容分组
            content_groups = {}
            for memory in all_memories:
                # 简单去重：相同内容
                content_key = memory.content.strip()
                if content_key not in content_groups:
                    content_groups[content_key] = []
                content_groups[content_key].append(memory)

            # 合并重复
            for content, memories in content_groups.items():
                if len(memories) > 1:
                    # 保留最新的，删除其他的
                    memories.sort(key=lambda m: m.timestamp, reverse=True)
                    keep = memories[0]
                    remove = memories[1:]

                    if not dry_run:
                        for mem in remove:
                            db.conn.execute("DELETE FROM memories WHERE id = ?", (mem.id,))
                            db.conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (mem.id,))

                    stats["merged"] += len(memories) - 1
                    stats["removed"] += len(memories) - 1

            if not dry_run:
                db.conn.commit()

        return stats

    def remove_old_memories(self, days: int = 365, dry_run: bool = False) -> dict:
        """
        删除过旧的记忆

        Parameters
        ----------
        days : int
            保留最近N天的记忆
        dry_run : bool
            是否为试运行

        Returns
        -------
        dict
            删除结果
        """
        stats = {"total": 0, "removed": 0, "kept": 0}

        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()

        with MemoryDatabase(self.db_path) as db:
            all_memories = db.list_memories(limit=10000)
            stats["total"] = len(all_memories)

            for memory in all_memories:
                if memory.timestamp < cutoff_date:
                    if not dry_run:
                        db.conn.execute("DELETE FROM memories WHERE id = ?", (memory.id,))
                        db.conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (memory.id,))
                    stats["removed"] += 1
                else:
                    stats["kept"] += 1

            if not dry_run:
                db.conn.commit()

        return stats

    def rebuild_index(self) -> dict:
        """
        重建索引

        Returns
        -------
        dict
            重建结果
        """
        stats = {"total": 0, "rebuilt": 0}

        with MemoryDatabase(self.db_path) as db:
            # 清空 FTS 索引
            db.conn.execute("DELETE FROM memories_fts")

            # 重新插入
            all_memories = db.list_memories(limit=10000)
            stats["total"] = len(all_memories)

            for memory in all_memories:
                db.conn.execute("""
                    INSERT INTO memories_fts (id, content, domain)
                    VALUES (?, ?, ?)
                """, (memory.id, memory.content, memory.domain))
                stats["rebuilt"] += 1

            db.conn.commit()

        return stats

    def generate_report(self) -> dict:
        """
        生成记忆统计报告

        Returns
        -------
        dict
            统计报告
        """
        report = {
            "total_memories": 0,
            "by_agent": {},
            "by_domain": {},
            "by_device": {},
            "by_confidence": {},
            "oldest_memory": None,
            "newest_memory": None,
            "average_content_length": 0
        }

        with MemoryDatabase(self.db_path) as db:
            all_memories = db.list_memories(limit=10000)
            report["total_memories"] = len(all_memories)

            if not all_memories:
                return report

            total_length = 0
            for memory in all_memories:
                # 按 Agent 统计
                report["by_agent"][memory.agent_id] = report["by_agent"].get(memory.agent_id, 0) + 1

                # 按领域统计
                report["by_domain"][memory.domain] = report["by_domain"].get(memory.domain, 0) + 1

                # 按设备统计
                report["by_device"][memory.source_device] = report["by_device"].get(memory.source_device, 0) + 1

                # 按置信度统计
                report["by_confidence"][memory.confidence] = report["by_confidence"].get(memory.confidence, 0) + 1

                # 内容长度
                total_length += len(memory.content)

                # 时间范围
                if report["oldest_memory"] is None or memory.timestamp < report["oldest_memory"]:
                    report["oldest_memory"] = memory.timestamp
                if report["newest_memory"] is None or memory.timestamp > report["newest_memory"]:
                    report["newest_memory"] = memory.timestamp

            report["average_content_length"] = total_length / len(all_memories)

        return report


# ---------------------------------------------------------------------------
# 便捷函数 - 数据保护
# ---------------------------------------------------------------------------

def backup_all(memory_root: Path = None) -> dict:
    """
    备份所有数据

    Parameters
    ----------
    memory_root : Path, optional
        记忆根目录

    Returns
    -------
    dict
        备份结果
    """
    if memory_root is None:
        ctx = get_loaded_context()
        memory_root = ctx.identity.memory_root

    protection = DataProtection(memory_root)
    results = {"backed_up": [], "errors": []}

    # 备份所有 Markdown 文件
    for md_file in memory_root.glob("memory_private*.md"):
        if md_file.suffix == ".md" and not md_file.name.endswith(".lock") and not md_file.name.endswith(".tmp"):
            try:
                backup_path = protection.backup_before_write(md_file)
                if backup_path:
                    results["backed_up"].append(str(backup_path))
            except Exception as e:
                results["errors"].append("{}: {}".format(md_file.name, e))

    # 备份 SQLite 数据库
    for db_file in memory_root.glob("*.db"):
        try:
            backup_path = protection.backup_before_write(db_file)
            if backup_path:
                results["backed_up"].append(str(backup_path))
        except Exception as e:
            results["errors"].append("{}: {}".format(db_file.name, e))

    return results


def check_integrity(memory_root: Path = None) -> dict:
    """
    检查数据完整性

    Parameters
    ----------
    memory_root : Path, optional
        记忆根目录

    Returns
    -------
    dict
        检查结果
    """
    if memory_root is None:
        ctx = get_loaded_context()
        memory_root = ctx.identity.memory_root

    checker = IntegrityChecker(memory_root)
    return checker.check_all()


def optimize_memories(db_path: Path = None, memory_root: Path = None) -> dict:
    """
    优化记忆

    Parameters
    ----------
    db_path : Path, optional
        数据库路径
    memory_root : Path, optional
        记忆根目录

    Returns
    -------
    dict
        优化结果
    """
    if memory_root is None:
        ctx = get_loaded_context()
        memory_root = ctx.identity.memory_root

    if db_path is None:
        db_path = memory_root / "memories.db"

    optimizer = MemoryOptimizer(db_path)
    results = {}

    # 压缩记忆
    results["compress"] = optimizer.compress_memories(dry_run=False)

    # 重建索引
    results["rebuild"] = optimizer.rebuild_index()

    return results


def get_memory_report(db_path: Path = None, memory_root: Path = None) -> dict:
    """
    获取记忆统计报告

    Parameters
    ----------
    db_path : Path, optional
        数据库路径
    memory_root : Path, optional
        记忆根目录

    Returns
    -------
    dict
        统计报告
    """
    if memory_root is None:
        ctx = get_loaded_context()
        memory_root = ctx.identity.memory_root

    if db_path is None:
        db_path = memory_root / "memories.db"

    optimizer = MemoryOptimizer(db_path)
    return optimizer.generate_report()


# ---------------------------------------------------------------------------
# 记忆重要性评分 - 新增
# ---------------------------------------------------------------------------

class ImportanceScorer:
    """记忆重要性评分器"""

    # 权重配置
    WEIGHTS = {
        "recency": 0.25,      # 时间衰减
        "frequency": 0.20,    # 访问频率
        "confidence": 0.15,   # 置信度
        "content_quality": 0.20,  # 内容质量
        "tag_richness": 0.10, # 标签丰富度
        "uniqueness": 0.10    # 独特性
    }

    def __init__(self, db_path: Path):
        """
        初始化评分器

        Parameters
        ----------
        db_path : Path
            数据库路径
        """
        self.db_path = db_path

    def calculate_score(self, memory: MemoryEntry, all_memories: list = None) -> float:
        """
        计算单条记忆的重要性分数

        Parameters
        ----------
        memory : MemoryEntry
            记忆条目
        all_memories : list, optional
            所有记忆列表（用于计算独特性）

        Returns
        -------
        float
            重要性分数 (0-1)
        """
        scores = {}

        # 1. 时间衰减分数
        scores["recency"] = self._score_recency(memory)

        # 2. 访问频率分数
        scores["frequency"] = self._score_frequency(memory)

        # 3. 置信度分数
        scores["confidence"] = self._score_confidence(memory)

        # 4. 内容质量分数
        scores["content_quality"] = self._score_content_quality(memory)

        # 5. 标签丰富度分数
        scores["tag_richness"] = self._score_tag_richness(memory)

        # 6. 独特性分数
        scores["uniqueness"] = self._score_uniqueness(memory, all_memories)

        # 加权求和
        total_score = sum(scores[k] * self.WEIGHTS[k] for k in self.WEIGHTS)

        return min(1.0, max(0.0, total_score))

    def _score_recency(self, memory: MemoryEntry) -> float:
        """计算时间衰减分数"""
        try:
            if memory.last_accessed:
                last_time = datetime.fromisoformat(memory.last_accessed.replace('Z', '+00:00'))
            else:
                last_time = datetime.fromisoformat(memory.timestamp.replace('Z', '+00:00'))

            now = datetime.now(timezone.utc)
            days_ago = (now - last_time).days

            # 指数衰减：30天内满分，之后快速下降
            if days_ago <= 7:
                return 1.0
            elif days_ago <= 30:
                return 0.9
            elif days_ago <= 90:
                return 0.7
            elif days_ago <= 180:
                return 0.5
            elif days_ago <= 365:
                return 0.3
            else:
                return 0.1
        except:
            return 0.5

    def _score_frequency(self, memory: MemoryEntry) -> float:
        """计算访问频率分数"""
        # 对数衰减：访问次数越多分数越高，但边际收益递减
        if memory.access_count == 0:
            return 0.1
        elif memory.access_count <= 3:
            return 0.3
        elif memory.access_count <= 10:
            return 0.6
        elif memory.access_count <= 50:
            return 0.8
        else:
            return 1.0

    def _score_confidence(self, memory: MemoryEntry) -> float:
        """计算置信度分数"""
        confidence_scores = {"high": 1.0, "medium": 0.6, "low": 0.3}
        return confidence_scores.get(memory.confidence, 0.5)

    def _score_content_quality(self, memory: MemoryEntry) -> float:
        """计算内容质量分数"""
        content = memory.content
        if not content:
            return 0.0

        # 基于内容长度和结构
        length = len(content)

        # 长度分数（太短或太长都不好）
        if length < 10:
            length_score = 0.2
        elif length < 50:
            length_score = 0.6
        elif length < 200:
            length_score = 1.0
        elif length < 500:
            length_score = 0.8
        else:
            length_score = 0.6

        # 结构分数（有标点、有分段）
        has_punctuation = any(c in content for c in '。，！？；：')
        has_multiple_sentences = content.count('。') > 1 or content.count('.') > 1

        structure_score = 0.5
        if has_punctuation:
            structure_score += 0.2
        if has_multiple_sentences:
            structure_score += 0.3

        return min(1.0, (length_score + structure_score) / 2)

    def _score_tag_richness(self, memory: MemoryEntry) -> float:
        """计算标签丰富度分数"""
        if not memory.tags:
            return 0.1

        tag_count = len(memory.tags)
        if tag_count == 1:
            return 0.4
        elif tag_count == 2:
            return 0.7
        elif tag_count >= 3:
            return 1.0
        return 0.5

    def _score_uniqueness(self, memory: MemoryEntry, all_memories: list = None) -> float:
        """计算独特性分数"""
        if not all_memories:
            return 0.5

        # 计算与其他记忆的内容相似度
        similar_count = 0
        content_words = set(memory.content.split())

        for other in all_memories:
            if other.id == memory.id:
                continue

            other_words = set(other.content.split())
            if not content_words or not other_words:
                continue

            # 简单的词重叠相似度
            overlap = len(content_words & other_words)
            similarity = overlap / min(len(content_words), len(other_words))

            if similarity > 0.7:
                similar_count += 1

        # 相似记忆越少，独特性越高
        if similar_count == 0:
            return 1.0
        elif similar_count <= 2:
            return 0.7
        elif similar_count <= 5:
            return 0.4
        else:
            return 0.2

    def score_all(self) -> list:
        """
        计算所有记忆的重要性分数

        Returns
        -------
        list
            (MemoryEntry, score) 元组列表
        """
        with MemoryDatabase(self.db_path) as db:
            all_memories = db.list_memories(limit=10000)

            scored = []
            for memory in all_memories:
                score = self.calculate_score(memory, all_memories)
                scored.append((memory, score))

            # 按分数降序排序
            scored.sort(key=lambda x: x[1], reverse=True)

            return scored


# ---------------------------------------------------------------------------
# 分层存储管理器 - 新增
# ---------------------------------------------------------------------------

class TieredStorageManager:
    """分层存储管理器"""

    def __init__(self, memory_root: Path, db_path: Path):
        """
        初始化分层存储管理器

        Parameters
        ----------
        memory_root : Path
            记忆根目录
        db_path : Path
            数据库路径
        """
        config = get_config()
        self.memory_root = memory_root
        self.db_path = db_path

        # 从配置读取分层阈值
        self.TIER_THRESHOLDS = {
            "hot": config.get("limits.hot_tier_days", 30),
            "warm": config.get("limits.warm_tier_days", 180),
            "cold": config.get("limits.cold_tier_days", 365)
        }

        archive_dir_name = config.get("paths.archive_dir", ".archive")
        self.archive_dir = memory_root / archive_dir_name
        self.archive_dir.mkdir(exist_ok=True)

    def classify_memories(self) -> dict:
        """
        分类记忆到不同层级

        Returns
        -------
        dict
            {tier: [MemoryEntry, ...]}
        """
        now = datetime.now(timezone.utc)
        tiers = {"hot": [], "warm": [], "cold": [], "archive": []}

        with MemoryDatabase(self.db_path) as db:
            all_memories = db.list_memories(limit=10000)

            for memory in all_memories:
                try:
                    if memory.last_accessed:
                        last_time = datetime.fromisoformat(memory.last_accessed.replace('Z', '+00:00'))
                    else:
                        last_time = datetime.fromisoformat(memory.timestamp.replace('Z', '+00:00'))

                    days_ago = (now - last_time).days

                    if days_ago <= self.TIER_THRESHOLDS["hot"]:
                        tiers["hot"].append(memory)
                    elif days_ago <= self.TIER_THRESHOLDS["warm"]:
                        tiers["warm"].append(memory)
                    elif days_ago <= self.TIER_THRESHOLDS["cold"]:
                        tiers["cold"].append(memory)
                    else:
                        tiers["archive"].append(memory)
                except:
                    tiers["warm"].append(memory)

        return tiers

    def archive_cold_memories(self, min_score: float = 0.3, dry_run: bool = False) -> dict:
        """
        归档冷数据

        Parameters
        ----------
        min_score : float
            最低重要性分数（低于此分数的冷数据才归档）
        dry_run : bool
            是否为试运行

        Returns
        -------
        dict
            归档结果
        """
        stats = {"total_cold": 0, "archived": 0, "kept": 0}

        tiers = self.classify_memories()
        cold_memories = tiers["cold"]

        stats["total_cold"] = len(cold_memories)

        # 计算重要性分数
        scorer = ImportanceScorer(self.db_path)
        scored_memories = []
        for memory in cold_memories:
            score = scorer.calculate_score(memory, cold_memories)
            scored_memories.append((memory, score))

        # 归档低分记忆
        for memory, score in scored_memories:
            if score < min_score:
                if not dry_run:
                    self._archive_memory(memory)
                stats["archived"] += 1
            else:
                stats["kept"] += 1

        return stats

    def _archive_memory(self, memory: MemoryEntry):
        """归档单条记忆"""
        # 按月份组织归档
        try:
            month_dir = memory.timestamp[:7]  # YYYY-MM
        except:
            month_dir = "unknown"

        archive_path = self.archive_dir / month_dir
        archive_path.mkdir(exist_ok=True)

        # 追加到归档文件
        archive_file = archive_path / "memories.md"
        entry_text = format_memory_entry(memory)

        with open(archive_file, "a", encoding="utf-8") as f:
            f.write(entry_text + "\n")

        # 从主数据库删除
        with MemoryDatabase(self.db_path) as db:
            db.conn.execute("DELETE FROM memories WHERE id = ?", (memory.id,))
            db.conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (memory.id,))
            db.conn.commit()

    def get_storage_stats(self) -> dict:
        """
        获取存储统计

        Returns
        -------
        dict
            存储统计
        """
        tiers = self.classify_memories()

        # 统计归档
        archive_count = 0
        for month_dir in self.archive_dir.iterdir():
            if month_dir.is_dir():
                archive_file = month_dir / "memories.md"
                if archive_file.exists():
                    text = archive_file.read_text(encoding="utf-8")
                    archive_count += text.count("---")

        return {
            "hot": len(tiers["hot"]),
            "warm": len(tiers["warm"]),
            "cold": len(tiers["cold"]),
            "archive": archive_count,
            "total": len(tiers["hot"]) + len(tiers["warm"]) + len(tiers["cold"]) + archive_count
        }


# ---------------------------------------------------------------------------
# 智能压缩器 - 新增
# ---------------------------------------------------------------------------

class SmartCompressor:
    """智能压缩器 - 保留重要记忆"""

    def __init__(self, db_path: Path, embedding_service: EmbeddingService = None):
        """
        初始化智能压缩器

        Parameters
        ----------
        db_path : Path
            数据库路径
        embedding_service : EmbeddingService, optional
            Embedding 服务实例
        """
        config = get_config()
        self.db_path = db_path
        self.embedding_service = embedding_service
        self.scorer = ImportanceScorer(db_path)
        self.default_similarity_threshold = config.get("compression.similarity_threshold", 0.7)

    def smart_compress(
        self,
        target_count: int = None,
        min_score: float = 0.3,
        similarity_threshold: float = None,
        dry_run: bool = False
    ) -> dict:
        """
        智能压缩

        Parameters
        ----------
        target_count : int, optional
            目标记忆数量（如果指定，会压缩到这个数量）
        min_score : float
            最低重要性分数（低于此分数的记忆会被考虑删除）
        similarity_threshold : float
            相似度阈值（高于此值的记忆会被合并）
        dry_run : bool
            是否为试运行

        Returns
        -------
        dict
            压缩结果
        """
        if similarity_threshold is None:
            similarity_threshold = self.default_similarity_threshold

        stats = {
            "total": 0,
            "duplicates_removed": 0,
            "similar_merged": 0,
            "low_score_removed": 0,
            "final_count": 0
        }

        with MemoryDatabase(self.db_path) as db:
            all_memories = db.list_memories(limit=10000)
            stats["total"] = len(all_memories)

            # 计算重要性分数
            scored_memories = []
            for memory in all_memories:
                score = self.scorer.calculate_score(memory, all_memories)
                scored_memories.append((memory, score))

            # 按分数降序排序
            scored_memories.sort(key=lambda x: x[1], reverse=True)

            # 第一步：删除完全重复的内容
            seen_contents = {}
            to_remove = []

            for memory, score in scored_memories:
                content_key = memory.content.strip()
                if content_key in seen_contents:
                    # 保留分数更高的那个
                    existing_score = seen_contents[content_key][1]
                    if score > existing_score:
                        # 删除旧的，保留新的
                        to_remove.append(seen_contents[content_key][0].id)
                        seen_contents[content_key] = (memory, score)
                    else:
                        # 删除新的
                        to_remove.append(memory.id)
                else:
                    seen_contents[content_key] = (memory, score)

            stats["duplicates_removed"] = len(to_remove)

            if not dry_run:
                for memory_id in to_remove:
                    db.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                    db.conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (memory_id,))

            # 第二步：合并相似内容（如果目标数量指定）
            if target_count and stats["total"] - stats["duplicates_removed"] > target_count:
                # 移除已删除的记忆
                remaining = [(m, s) for m, s in scored_memories if m.id not in to_remove]

                # 找到相似记忆并合并
                merged_ids = set()
                for i, (mem1, score1) in enumerate(remaining):
                    if mem1.id in merged_ids:
                        continue

                    for j, (mem2, score2) in enumerate(remaining[i+1:], i+1):
                        if mem2.id in merged_ids:
                            continue

                        # 检查相似度
                        if self._are_similar(mem1, mem2):
                            # 合并到分数更高的那个
                            if score1 >= score2:
                                merged_ids.add(mem2.id)
                            else:
                                merged_ids.add(mem1.id)
                                break

                            stats["similar_merged"] += 1

                            # 检查是否达到目标
                            current_count = stats["total"] - stats["duplicates_removed"] - stats["similar_merged"]
                            if current_count <= target_count:
                                break

                    if target_count and (stats["total"] - stats["duplicates_removed"] - stats["similar_merged"]) <= target_count:
                        break

                if not dry_run:
                    for memory_id in merged_ids:
                        db.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                        db.conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (memory_id,))

            # 第三步：删除低分记忆（如果还需要进一步压缩）
            if target_count:
                current_count = stats["total"] - stats["duplicates_removed"] - stats["similar_merged"]
                if current_count > target_count:
                    # 移除已处理的记忆
                    removed_ids = set(to_remove)
                    remaining = [(m, s) for m, s in scored_memories if m.id not in removed_ids]

                    # 按分数排序，删除低分的
                    remaining.sort(key=lambda x: x[1])

                    for memory, score in remaining:
                        if score < min_score:
                            if not dry_run:
                                db.conn.execute("DELETE FROM memories WHERE id = ?", (memory.id,))
                                db.conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (memory.id,))
                            stats["low_score_removed"] += 1

                            current_count = stats["total"] - stats["duplicates_removed"] - stats["similar_merged"] - stats["low_score_removed"]
                            if current_count <= target_count:
                                break

            if not dry_run:
                db.conn.commit()

            stats["final_count"] = stats["total"] - stats["duplicates_removed"] - stats["similar_merged"] - stats["low_score_removed"]

        return stats

    def _are_similar(self, mem1: MemoryEntry, mem2: MemoryEntry) -> bool:
        """检查两条记忆是否相似"""
        # 简单的词重叠相似度
        words1 = set(mem1.content.split())
        words2 = set(mem2.content.split())

        if not words1 or not words2:
            return False

        overlap = len(words1 & words2)
        similarity = overlap / min(len(words1), len(words2))

        return similarity >= 0.7

    def get_compression_preview(self, target_count: int = None) -> dict:
        """
        预览压缩结果

        Parameters
        ----------
        target_count : int, optional
            目标记忆数量

        Returns
        -------
        dict
            预览结果
        """
        return self.smart_compress(
            target_count=target_count,
            dry_run=True
        )


# ---------------------------------------------------------------------------
# 搜索优化器 - 新增
# ---------------------------------------------------------------------------

class SearchOptimizer:
    """搜索优化器"""

    def __init__(self, db_path: Path):
        """
        初始化搜索优化器

        Parameters
        ----------
        db_path : Path
            数据库路径
        """
        self.db_path = db_path
        self._query_cache = {}
        self._cache_max_size = 100

    def optimized_search(
        self,
        query: str,
        tags: list = None,
        domain: str = None,
        limit: int = 10,
        use_cache: bool = True
    ) -> list:
        """
        优化搜索

        Parameters
        ----------
        query : str
            搜索查询
        tags : list, optional
            标签过滤
        domain : str, optional
            领域过滤
        limit : int
            返回数量
        use_cache : bool
            是否使用缓存

        Returns
        -------
        list
            搜索结果
        """
        # 生成缓存键
        cache_key = self._generate_cache_key(query, tags, domain, limit)

        # 检查缓存
        if use_cache and cache_key in self._query_cache:
            return self._query_cache[cache_key]

        # 执行搜索
        with MemoryDatabase(self.db_path) as db:
            results = db.search_by_keyword(
                query=query,
                tags=tags,
                domain=domain,
                limit=limit
            )

        # 更新缓存
        if use_cache:
            self._update_cache(cache_key, results)

        return results

    def _generate_cache_key(self, query: str, tags: list, domain: str, limit: int) -> str:
        """生成缓存键"""
        tags_str = ",".join(sorted(tags)) if tags else ""
        return "{}|{}|{}|{}".format(query, tags_str, domain or "", limit)

    def _update_cache(self, key: str, value: list):
        """更新缓存"""
        # LRU 缓存：如果缓存满了，删除最旧的
        if len(self._query_cache) >= self._cache_max_size:
            oldest_key = next(iter(self._query_cache))
            del self._query_cache[oldest_key]

        self._query_cache[key] = value

    def clear_cache(self):
        """清空缓存"""
        self._query_cache.clear()

    def get_cache_stats(self) -> dict:
        """获取缓存统计"""
        return {
            "size": len(self._query_cache),
            "max_size": self._cache_max_size,
            "hit_rate": 0  # TODO: 实现命中率统计
        }


# ---------------------------------------------------------------------------
# 便捷函数 - 智能管理
# ---------------------------------------------------------------------------

def smart_compress(
    target_count: int = None,
    min_score: float = 0.3,
    dry_run: bool = False,
    db_path: Path = None,
    memory_root: Path = None
) -> dict:
    """
    智能压缩记忆

    Parameters
    ----------
    target_count : int, optional
        目标记忆数量
    min_score : float
        最低重要性分数
    dry_run : bool
        是否为试运行（不实际执行）
    db_path : Path, optional
        数据库路径
    memory_root : Path, optional
        记忆根目录

    Returns
    -------
    dict
        压缩结果
    """
    if memory_root is None:
        ctx = get_loaded_context()
        memory_root = ctx.identity.memory_root

    if db_path is None:
        db_path = memory_root / "memories.db"

    logger = get_logger()
    logger.info("开始智能压缩: target={}, min_score={}, dry_run={}".format(
        target_count, min_score, dry_run))

    compressor = SmartCompressor(db_path)
    result = compressor.smart_compress(
        target_count=target_count,
        min_score=min_score,
        dry_run=dry_run
    )

    logger.info("智能压缩完成: 原始={}, 最终={}, 删除重复={}, 合并相似={}, 删除低分={}".format(
        result["total"], result["final_count"],
        result["duplicates_removed"], result["similar_merged"],
        result["low_score_removed"]))
    return result


def archive_cold_memories(min_score: float = 0.3, memory_root: Path = None) -> dict:
    """
    归档冷数据

    Parameters
    ----------
    min_score : float
        最低重要性分数
    memory_root : Path, optional
        记忆根目录

    Returns
    -------
    dict
        归档结果
    """
    if memory_root is None:
        ctx = get_loaded_context()
        memory_root = ctx.identity.memory_root

    logger = get_logger()
    logger.info("开始归档冷数据: min_score={}".format(min_score))

    db_path = memory_root / "memories.db"
    manager = TieredStorageManager(memory_root, db_path)
    result = manager.archive_cold_memories(min_score=min_score)

    logger.info("归档完成: 冷数据={}, 已归档={}, 保留={}".format(
        result["total_cold"], result["archived"], result["kept"]))
    return result


def get_importance_scores(db_path: Path = None, memory_root: Path = None) -> list:
    """
    获取所有记忆的重要性分数

    Parameters
    ----------
    db_path : Path, optional
        数据库路径
    memory_root : Path, optional
        记忆根目录

    Returns
    -------
    list
        (MemoryEntry, score) 元组列表
    """
    if memory_root is None:
        ctx = get_loaded_context()
        memory_root = ctx.identity.memory_root

    if db_path is None:
        db_path = memory_root / "memories.db"

    scorer = ImportanceScorer(db_path)
    return scorer.score_all()


# ---------------------------------------------------------------------------
# 记忆过期机制 - 新增
# ---------------------------------------------------------------------------

def expire_old_memories(memory_root: Path = None, dry_run: bool = False) -> dict:
    """
    清理过期记忆

    根据配置的 max_memory_age_days，将超过有效期的记忆归档。

    Parameters
    ----------
    memory_root : Path, optional
        记忆根目录
    dry_run : bool
        是否为试运行（不实际删除）

    Returns
    -------
    dict
        清理结果
    """
    if memory_root is None:
        ctx = get_loaded_context()
        memory_root = ctx.identity.memory_root

    config = get_config()
    max_age_days = config.get("limits.max_memory_age_days", 365)
    logger = get_logger()

    db_path = memory_root / "memories.db"
    cutoff_date = datetime.now() - timedelta(days=max_age_days)
    cutoff_iso = cutoff_date.isoformat()

    logger.info("清理过期记忆: max_age_days={}, cutoff={}".format(max_age_days, cutoff_iso))

    result = {
        "total_checked": 0,
        "expired_found": 0,
        "archived": 0,
        "errors": []
    }

    with MemoryDatabase(db_path) as db:
        all_memories = db.list_memories(limit=10000)
        result["total_checked"] = len(all_memories)

        expired = []
        for memory in all_memories:
            if memory.timestamp < cutoff_iso:
                expired.append(memory)

        result["expired_found"] = len(expired)

        if expired and not dry_run:
            # 归档过期记忆
            archive_dir = memory_root / config.get("paths.archive_dir", ".archive")
            archive_dir.mkdir(exist_ok=True)

            # 按月归档
            monthly_groups = {}
            for memory in expired:
                try:
                    dt = datetime.fromisoformat(memory.timestamp)
                    month_key = dt.strftime("%Y-%m")
                except:
                    month_key = "unknown"

                if month_key not in monthly_groups:
                    monthly_groups[month_key] = []
                monthly_groups[month_key].append(memory)

            for month_key, memories in monthly_groups.items():
                month_dir = archive_dir / month_key
                month_dir.mkdir(exist_ok=True)
                archive_file = month_dir / "memories.md"

                existing_content = ""
                if archive_file.exists():
                    existing_content = archive_file.read_text(encoding="utf-8")

                for memory in memories:
                    existing_content = append_memory_entry(existing_content, memory)

                    # 从活动数据库中删除
                    db.conn.execute("DELETE FROM memories WHERE id = ?", (memory.id,))
                    db.conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (memory.id,))

                    result["archived"] += 1

                archive_file.write_text(existing_content, encoding="utf-8")

            db.conn.commit()
            logger.info("过期记忆已归档: {} 条".format(result["archived"]))
        elif expired and dry_run:
            logger.info("试运行: 发现 {} 条过期记忆".format(len(expired)))

    return result


# ---------------------------------------------------------------------------
# 鲁棒性 Agent 路径探测
# ---------------------------------------------------------------------------

def _verify_agent_signature(path: Path, profile: dict) -> bool:
    """
    验证路径是否真的是该 agent 的记忆目录

    Parameters
    ----------
    path : Path
        待验证的路径
    profile : dict
        agent 检测配置（signature_file, signature_content, signature_glob）

    Returns
    -------
    bool
        路径是否匹配
    """
    if not path.exists():
        return False
    if "signature_file" in profile:
        sig_file = path / profile["signature_file"]
        if not sig_file.exists():
            return False
        if "signature_content" in profile:
            try:
                content = sig_file.read_text(encoding="utf-8")[:500]
                return profile["signature_content"] in content
            except (OSError, UnicodeDecodeError):
                return False
        return True
    if "signature_glob" in profile:
        return any(path.glob(profile["signature_glob"]))
    return path.exists()


def detect_agents(
    config: ConfigManager = None,
    force_redetect: bool = False,
    write_cache: bool = True
) -> dict:
    """
    鲁棒性 Agent 路径探测

    不依赖硬编码路径，通过候选路径 + 特征验证动态发现本机安装的 Agent。
    结果缓存到 .detected_agents.json，支持 TTL 过期和手动覆盖。

    Parameters
    ----------
    config : ConfigManager, optional
        配置管理器，默认使用全局实例
    force_redetect : bool
        强制重新检测，忽略缓存

    Returns
    -------
    dict
        {agent_id: {"path": str, "memory_files": list, "detected_at": str}}
    """
    if config is None:
        config = get_config()

    logger = get_logger()

    # 检查手动覆盖（从 config.json 和 sync_settings.json 合并）
    overrides = dict(config.get("agent_overrides", {}))
    # 也读取 GUI 设置中保存的 override
    # 修复：用 safe_io.get_data_root() 解析实际路径，而非硬编码旧路径 ~/.agent_memory/
    sync_settings_path = get_data_root() / "sync_settings.json"
    if sync_settings_path.exists():
        try:
            with open(sync_settings_path, "r", encoding="utf-8") as f:
                sync_settings = json.load(f)
            gui_overrides = sync_settings.get("agent_overrides", {})
            for k, v in gui_overrides.items():
                if k not in overrides or not overrides[k]:
                    overrides[k] = v
        except (json.JSONDecodeError, OSError):
            pass
    # 兼容旧路径（迁移残留）
    legacy_settings_path = Path.home() / ".agent_memory" / "sync_settings.json"
    if legacy_settings_path.exists() and legacy_settings_path != sync_settings_path:
        try:
            with open(legacy_settings_path, "r", encoding="utf-8") as f:
                legacy_settings = json.load(f)
            legacy_overrides = legacy_settings.get("agent_overrides", {})
            for k, v in legacy_overrides.items():
                if k not in overrides or not overrides[k]:
                    overrides[k] = v
        except (json.JSONDecodeError, OSError):
            pass

    # 检查缓存
    cache_path = Path.home() / ".agent_memory" / ".detected_agents.json"
    cache_ttl_hours = config.get("sync_tool.cache_ttl_hours", 24)

    if not force_redetect and cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            detected_at = datetime.fromisoformat(cache.get("detected_at", "2000-01-01T00:00:00+00:00"))
            age_hours = (datetime.now(timezone.utc) - detected_at).total_seconds() / 3600
            if age_hours < cache_ttl_hours:
                result = cache.get("agents", {})
                # 清除已失效的 override（配置中已移除的覆盖）
                stale_overrides = [
                    aid for aid, info in result.items()
                    if info.get("source") == "override" and aid not in overrides
                ]
                for aid in stale_overrides:
                    del result[aid]
                # 合并当前手动覆盖
                for agent_id, override_path in overrides.items():
                    if override_path and Path(override_path).exists():
                        result[agent_id] = {
                            "path": override_path,
                            "memory_files": [],
                            "detected_at": detected_at.isoformat(),
                            "source": "override"
                        }
                logger.info("使用缓存的 Agent 检测结果 (age={:.1f}h)".format(age_hours))
                return result
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    # 执行检测
    profiles = config.get("agent_detection", {})
    if not profiles:
        # fallback 到传统 _AGENT_SUBDIRS 方式
        logger.warning("agent_detection 配置为空，使用传统检测方式")
        return _detect_agents_legacy(config)

    found = {}
    home = Path.home()

    for agent_id, profile in profiles.items():
        # 手动覆盖优先
        if agent_id in overrides and overrides[agent_id]:
            override_path = Path(overrides[agent_id])
            if override_path.exists():
                found[agent_id] = {
                    "path": str(override_path),
                    "memory_files": [],
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "source": "override"
                }
                logger.info("Agent {} 使用手动覆盖路径: {}".format(agent_id, override_path))
                continue

        # 遍历候选路径
        for pattern in profile.get("candidate_paths", []):
            path = Path(os.path.expanduser(pattern))
            if _verify_agent_signature(path, profile):
                memory_files = []

                # SQLite 类型的 Agent（如 CodePilot）
                if profile.get("storage_type") == "sqlite":
                    sig_file = profile.get("signature_file", "")
                    db_path = path / sig_file
                    if db_path.exists():
                        export_path = export_codepilot_memory(db_path)
                        memory_files = [str(export_path)]
                        logger.info("Agent {} SQLite 导出: {}".format(agent_id, export_path))
                else:
                    # 扫描记忆文件
                    memory_files = _filter_agent_memory_files(
                        agent_id,
                        _scan_agent_memory_files(agent_id, path)
                    )

                found[agent_id] = {
                    "path": str(path),
                    "memory_files": memory_files,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "source": "auto"
                }
                logger.info("发现 Agent {}: {}".format(agent_id, path))
                break

    # 处理自定义 Agent：overrides 里有但 config.json agent_detection 没有的 key
    # 这些是用户在 GUI 里手动添加的自定义 agent（如 openclaw 或任意新 agent）
    for custom_id, custom_path_str in overrides.items():
        if custom_id in found or not custom_path_str:
            continue
        if custom_id in profiles:
            continue  # 已在 profile 循环里处理过
        custom_path = Path(custom_path_str)
        if custom_path.exists():
            # 扫描自定义 agent 路径下的 .md 文件作为记忆文件
            memory_files = []
            if custom_path.is_dir():
                for md_file in custom_path.rglob("*.md"):
                    memory_files.append(str(md_file))
            found[custom_id] = {
                "path": str(custom_path),
                "memory_files": memory_files,
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "source": "custom_override"
            }
            logger.info("自定义 Agent {} 使用手动路径: {}".format(custom_id, custom_path))

    # 通用发现：扫描未被已知 profile 覆盖的 AI 工具目录
    found = _discover_generic_agents(found, home, logger)

    # 保存缓存（测试时可禁用）
    if write_cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "agents": found
                }, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("保存检测缓存失败: {}".format(e))

    return found


def _discover_generic_agents(found: dict, home: Path, logger) -> dict:
    """
    通用 Agent 发现：扫描常见 AI 工具目录，查找包含记忆文件的未识别 Agent。

    已知 Agent 已通过 config.json 精确检测，这里只处理未知目录。
    """
    # 常见 AI 工具的配置/数据目录
    generic_candidates = [
        ("~/.config", "config"),
        ("~/AppData/Local", "appdata_local"),
        ("~/AppData/Roaming", "appdata_roaming"),
    ]

    # AI 工具名称关键词（目录名包含这些词的会被识别为潜在 Agent）
    ai_keywords = [
        "agent", "ai", "llm", "gpt", "copilot", "cursor", "windsurf",
        "cline", "continue", "aider", "roo", "codex", "devin", "replit",
        "tabby", "supermaven", "codeium", "mentat", "openhands",
        "sweep", "factory", "magic", "augment", "poolside", "codepilot",
        "codebuddy", "ima",
    ]

    # 排除的目录名（非 Agent 工具，或已知浏览器壳应用）
    exclude_names = {
        "microsoft", "google", "mozilla", "discord", "slack", "spotify",
        "obsidian", "notion", "figma", "docker", "node", "npm", "pip",
        "python", "java", "rust", "go", "dotnet", "nuget", "pipx",
        "windows", "temp", "cache", "crashdumps", "logs",
        # Chromium 壳应用（含 AI 关键词但实为浏览器，无 .md 记忆文件）
        "ima.copilot", "codepilot-updater", "codebuddyextension",
        # Chromium 内部目录
        "user data", "application", "extensions_crx_cache",
        "component_crx_cache", "shadercache", "graphitedawncache",
        "grshadercache", "crashpad", "bugly", "imsdk", "reshub",
        "imainfo", "recording", "widevinecdm", "safe browsing",
        "firstpartysetspreloaded", "optimizationhints", "orig trials",
        "pkimetadata", "trusttokenkeycommitments", "wasmttsengine",
        "zxcvbndata", "meipreload", "ondeviceheadsuggestmodel",
        "privacyboxattestationspreloaded", "subresource filter",
        "filetypepolicies", "captcha providers", "actorsafetylists",
        "amountextractionheuristicregexes", "certificaterevocation",
        "crowd deny", "hyphen-data", "rdelivery", "segmentation platform",
        "safetytips", "sslassistant", "tdosconfig", "trust token",
        "updateextensions", "key info",
    }

    detected_paths = {info["path"] for info in found.values()}

    for pattern, source in generic_candidates:
        base = Path(pattern).expanduser()
        if not base.exists():
            continue
        try:
            for item in base.iterdir():
                if not item.is_dir():
                    continue
                name_lower = item.name.lower()

                # 跳过已检测到的路径
                if str(item) in detected_paths:
                    continue

                # 跳过排除列表
                if name_lower in exclude_names:
                    continue

                # 检查是否包含 AI 关键词
                is_ai = any(kw in name_lower for kw in ai_keywords)
                if not is_ai:
                    continue

                # 检查是否有记忆文件
                memory_files = _scan_generic_memory_files(item)
                if memory_files:
                    agent_id = "generic-" + name_lower
                    found[agent_id] = {
                        "path": str(item),
                        "memory_files": memory_files,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "source": "generic-discovery"
                    }
                    detected_paths.add(str(item))
                    logger.info("通用发现 Agent {}: {}".format(agent_id, item))
        except (PermissionError, OSError):
            continue

    return found


def _scan_generic_memory_files(path: Path) -> list:
    """扫描目录中的记忆文件"""
    memory_files = []
    candidates = ["MEMORY.md", "memory.md", "memories.md", "user_profile.md",
                   "USER.md", "user.md", ".aider.memory.md"]
    max_file_size = 10 * 1024 * 1024  # 10MB 上限，避免误读超大文件
    for c in candidates:
        p = path / c
        if p.exists():
            try:
                if p.stat().st_size <= max_file_size:
                    memory_files.append(str(p))
                else:
                    logger = get_logger()
                    logger.debug("跳过超大记忆文件 ({}MB): {}".format(
                        p.stat().st_size / 1024 / 1024, p))
            except OSError:
                pass

    # 也检查 memory/ 子目录
    mem_dir = path / "memory"
    if mem_dir.exists():
        for md in mem_dir.glob("*.md"):
            try:
                if md.stat().st_size <= max_file_size:
                    memory_files.append(str(md))
            except OSError:
                pass

    return memory_files


def _detect_agents_legacy(config: ConfigManager) -> dict:
    """传统检测方式（fallback）"""
    registry = AgentRegistry()
    registry.scan_local()
    result = {}
    for agent_info in registry.list_all():
        agent_id = agent_info["agent_id"]
        result[agent_id] = {
            "path": agent_info.get("installation_path", ""),
            "memory_files": agent_info.get("memory_files", []),
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "source": "legacy"
        }
    return result


def _scan_agent_memory_files(agent_id: str, install_path: Path) -> list:
    """扫描指定 Agent 的记忆文件"""
    memory_files = []
    path_str = str(install_path).lower()

    if "hermes" in agent_id or "hermes" in path_str:
        for md_file in install_path.glob("*.md"):
            memory_files.append(str(md_file))
    elif "claude" in agent_id or "claude" in path_str:
        if "projects" in path_str:
            # 已经是 projects 目录
            for mem_file in install_path.glob("*/memory/MEMORY.md"):
                memory_files.append(str(mem_file))
            for mem_file in install_path.glob("*/memory/*.md"):
                if str(mem_file) not in memory_files:
                    memory_files.append(str(mem_file))
            for jsonl_file in install_path.glob("*/*.jsonl"):
                memory_files.append(str(jsonl_file))
        else:
            projects_dir = install_path / "projects"
            if projects_dir.exists():
                for mem_file in projects_dir.glob("*/memory/MEMORY.md"):
                    memory_files.append(str(mem_file))
                for mem_file in projects_dir.glob("*/memory/*.md"):
                    if str(mem_file) not in memory_files:
                        memory_files.append(str(mem_file))
                for jsonl_file in projects_dir.glob("*/*.jsonl"):
                    memory_files.append(str(jsonl_file))
    elif "trae" in agent_id or "trae" in path_str:
        if install_path.name == "memory":
            mem_dir = install_path
        else:
            mem_dir = install_path / "memory"
        if mem_dir.exists():
            user_profile = mem_dir / "user_profile.md"
            if user_profile.exists():
                memory_files.append(str(user_profile))
            for topics_file in mem_dir.glob("projects/*/topics.md"):
                memory_files.append(str(topics_file))
            for topics_file in mem_dir.glob("projects/*/*/topics.md"):
                memory_files.append(str(topics_file))
            for proj_mem in mem_dir.glob("projects/*/project_memory.md"):
                memory_files.append(str(proj_mem))
    elif "codebuddy" in agent_id or "memery" in path_str:
        # CodeBuddy 记忆文件：~/.codebuddy/memery/*_memery.md
        for md_file in install_path.glob("*_memery.md"):
            memory_files.append(str(md_file))
        for md_file in install_path.glob("*.md"):
            if str(md_file) not in memory_files:
                memory_files.append(str(md_file))
    else:
        candidates = ["MEMORY.md", "memory.md", "memories.md", "USER.md", "user.md"]
        for c in candidates:
            p = install_path / c
            if p.exists():
                memory_files.append(str(p))

    return memory_files


def _sanitize_sensitive(text: str) -> str:
    """过滤文本中的敏感信息（API 密钥、密码等）"""
    import re
    # API 密钥模式：sk-xxx, ms-xxx, Bearer xxx 等
    patterns = [
        (r'(sk-[a-zA-Z0-9]{20,})', 'sk-***REDACTED***'),
        (r'(ms-[a-zA-Z0-9-]{20,})', 'ms-***REDACTED***'),
        (r'(Bearer\s+[a-zA-Z0-9_-]{20,})', 'Bearer ***REDACTED***'),
        (r'(api[_-]?key[=:]\s*\S+)', 'api_key=***REDACTED***'),
        (r'(password[=:]\s*\S+)', 'password=***REDACTED***'),
        (r'(token[=:]\s*[a-zA-Z0-9_-]{20,})', 'token=***REDACTED***'),
        (r'(secret[=:]\s*\S+)', 'secret=***REDACTED***'),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def export_codepilot_memory(db_path: Path, output_path: Path = None) -> Path:
    """
    从 CodePilot SQLite 数据库导出对话历史为 Markdown 文件

    使用只读模式连接，避免与 CodePilot 进程争抢数据库锁。
    同时读取同目录下的 MEMORY.md（限制最大 1MB，避免超大文件耗尽内存）。

    Parameters
    ----------
    db_path : Path
        codepilot.db 路径
    output_path : Path, optional
        输出文件路径，默认为数据根目录下的 codepilot_export.md

    Returns
    -------
    Path
        导出的 Markdown 文件路径
    """
    import sqlite3
    from safe_io import get_data_root

    logger = get_logger()

    if output_path is None:
        output_path = get_data_root() / "codepilot_export.md"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 只读 URI 连接：mode=ro + immutable=1，不争抢 WAL 锁
        db_uri = "file:{}?mode=ro&immutable=1".format(db_path)
        conn = sqlite3.connect(db_uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=1")

        # 获取有实际消息的会话
        sessions = conn.execute("""
            SELECT s.id, s.title, s.created_at, s.model,
                   COUNT(m.id) as msg_count
            FROM chat_sessions s
            JOIN messages m ON m.session_id = s.id
            WHERE m.role IN ('user', 'assistant')
            GROUP BY s.id
            HAVING msg_count > 0
            ORDER BY s.updated_at DESC
            LIMIT 100
        """).fetchall()

        lines = ["# CodePilot Memory\n"]
        total_entries = 0

        for session in sessions:
            title = session["title"] or "Untitled"
            created = session["created_at"] or ""
            model = session["model"] or ""

            # 获取该会话的最后几轮对话（最多最近 10 条）
            messages = conn.execute("""
                SELECT role, content, created_at
                FROM messages
                WHERE session_id = ? AND role IN ('user', 'assistant')
                ORDER BY created_at DESC
                LIMIT 10
            """, (session["id"],)).fetchall()

            if not messages:
                continue

            # 反转为时间正序
            messages = list(reversed(messages))

            lines.append("## {}\n".format(title[:80]))
            lines.append("Date: {} | Model: {}\n".format(created[:10], model))

            for msg in messages:
                content = msg["content"] or ""
                # 过滤敏感信息
                content = _sanitize_sensitive(content)
                # 截断过长的内容
                if len(content) > 1000:
                    content = content[:1000] + "..."
                role = "User" if msg["role"] == "user" else "Assistant"
                lines.append("**{}**: {}\n".format(role, content))
                total_entries += 1

            lines.append("")

        conn.close()

        # 同时读取同目录下的 MEMORY.md（限制 1MB，避免超大文件）
        memory_md = db_path.parent / "MEMORY.md"
        if memory_md.exists():
            try:
                file_size = memory_md.stat().st_size
                if file_size > 1 * 1024 * 1024:
                    # 超过 1MB 只读取最后 512KB（最近写入的内容）
                    lines.append("\n## MEMORY.md (truncated, last 512KB of {:.1f}MB)\n".format(
                        file_size / 1024 / 1024))
                    with open(memory_md, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(max(0, file_size - 512 * 1024))
                        content = f.read()
                        content = _sanitize_sensitive(content)
                        lines.append(content)
                else:
                    content = memory_md.read_text(encoding="utf-8", errors="replace")
                    content = _sanitize_sensitive(content)
                    lines.append("\n## MEMORY.md\n")
                    lines.append(content)
            except OSError as e:
                logger.warning("读取 CodePilot MEMORY.md 失败: {}".format(e))

        if not _safe_write_text(output_path, "\n".join(lines), encoding="utf-8"):
            logger.warning("CodePilot 导出文件写入失败（可能被锁定）: {}".format(output_path))
        else:
            logger.info("CodePilot 导出完成: {} 条对话, 输出到 {}".format(total_entries, output_path))
        return output_path

    except sqlite3.OperationalError as e:
        logger.error("CodePilot 数据库读取失败 (可能被锁定): {}".format(e))
        _safe_write_text(
            output_path,
            "# CodePilot Memory\n\nExport failed (database locked): {}\n".format(e),
            encoding="utf-8"
        )
        return output_path
    except Exception as e:
        logger.error("CodePilot 导出异常: {}".format(e), exc_info=True)
        _safe_write_text(
            output_path,
            "# CodePilot Memory\n\nExport failed: {}\n".format(e),
            encoding="utf-8"
        )
        return output_path


def content_hash(text: str) -> str:
    """计算内容的 SHA-256 前 16 位哈希"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def check_onedrive_conflicts(root: Path) -> list:
    """
    扫描 OneDrive 冲突文件

    Parameters
    ----------
    root : Path
        扫描根目录

    Returns
    -------
    list
        冲突文件路径列表
    """
    if not root.exists():
        return []
    return list(root.rglob("* (conflicted copy)*"))


# ---------------------------------------------------------------------------
# 健康检查 - 新增
# ---------------------------------------------------------------------------

def health_check(memory_root: Path = None) -> dict:
    """
    系统健康检查

    检查记忆系统的各个组件是否正常工作。

    Parameters
    ----------
    memory_root : Path, optional
        记忆根目录

    Returns
    -------
    dict
        健康检查结果
    """
    if memory_root is None:
        ctx = get_loaded_context()
        memory_root = ctx.identity.memory_root

    config = get_config()
    logger = get_logger()

    result = {
        "status": "healthy",
        "checks": {},
        "warnings": [],
        "errors": []
    }

    # 检查 1: 配置文件
    try:
        config.load()
        result["checks"]["config"] = "ok"
    except Exception as e:
        result["checks"]["config"] = "error"
        result["errors"].append("配置文件错误: {}".format(e))
        result["status"] = "unhealthy"

    # 检查 2: 数据库连接
    db_path = memory_root / "memories.db"
    try:
        with MemoryDatabase(db_path) as db:
            count = len(db.list_memories(limit=1))
            result["checks"]["database"] = "ok"
            result["checks"]["memory_count"] = count
    except Exception as e:
        result["checks"]["database"] = "error"
        result["errors"].append("数据库错误: {}".format(e))
        result["status"] = "unhealthy"

    # 检查 3: 记忆文件
    memory_files = list(memory_root.glob("memory_private*.md"))
    result["checks"]["memory_files"] = len(memory_files)
    if not memory_files:
        result["warnings"].append("未找到记忆文件")

    # 检查 4: 磁盘空间
    try:
        import shutil
        total, used, free = shutil.disk_usage(memory_root)
        free_gb = free / (1024 ** 3)
        result["checks"]["disk_free_gb"] = round(free_gb, 2)
        if free_gb < 1:
            result["warnings"].append("磁盘空间不足: {:.2f} GB".format(free_gb))
    except Exception:
        pass

    # 检查 5: 备份目录
    backup_dir = memory_root / config.get("paths.backup_dir", ".backups")
    if backup_dir.exists():
        backup_count = len(list(backup_dir.iterdir()))
    else:
        backup_count = 0
        result["warnings"].append("备份目录不存在")
    result["checks"]["backup_count"] = backup_count

    # 检查 6: 锁文件
    lock_dir = memory_root / config.get("paths.lock_dir", ".locks")
    if lock_dir.exists():
        lock_files = list(lock_dir.glob("*.lock"))
        if len(lock_files) > 0:
            result["warnings"].append("存在 {} 个活跃锁文件".format(len(lock_files)))
    else:
        lock_files = []
    result["checks"]["active_locks"] = len(lock_files)

    # 检查 7: 日志目录
    log_dir = Path(__file__).parent / config.get("paths.log_dir", ".logs")
    if log_dir.exists():
        result["checks"]["log_dir"] = "ok"
    else:
        result["warnings"].append("日志目录不存在")

    if result["warnings"] and result["status"] == "healthy":
        result["status"] = "warning"

    logger.info("健康检查完成: status={}, warnings={}, errors={}".format(
        result["status"], len(result["warnings"]), len(result["errors"])))

    return result


# ---------------------------------------------------------------------------
# 数据库版本管理 - 新增
# ---------------------------------------------------------------------------

def get_db_version(db_path: Path) -> int:
    """
    获取数据库版本

    Parameters
    ----------
    db_path : Path
        数据库路径

    Returns
    -------
    int
        数据库版本号，如果不存在返回 0
    """
    try:
        with MemoryDatabase(db_path) as db:
            cursor = db.conn.execute(
                "SELECT value FROM metadata WHERE key = 'db_version'"
            )
            row = cursor.fetchone()
            if row:
                return int(row[0])
    except Exception:
        pass
    return 0


def set_db_version(db_path: Path, version: int):
    """
    设置数据库版本

    Parameters
    ----------
    db_path : Path
        数据库路径
    version : int
        版本号
    """
    with MemoryDatabase(db_path) as db:
        db.conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            ("db_version", str(version))
        )
        db.conn.commit()


def migrate_database(db_path: Path) -> dict:
    """
    数据库迁移

    检查并执行必要的数据库迁移。

    Parameters
    ----------
    db_path : Path
        数据库路径

    Returns
    -------
    dict
        迁移结果
    """
    config = get_config()
    target_version = config.get("database.version", 1)
    current_version = get_db_version(db_path)

    result = {
        "current_version": current_version,
        "target_version": target_version,
        "migrations_applied": [],
        "already_up_to_date": current_version >= target_version
    }

    if current_version >= target_version:
        return result

    logger = get_logger()
    logger.info("数据库迁移: v{} -> v{}".format(current_version, target_version))

    with MemoryDatabase(db_path) as db:
        # 迁移 v0 -> v1: 添加 metadata 表
        # 注意: _init_db 中已创建 metadata 表, 这里只插入版本行
        if current_version < 1:
            db.conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("db_version", "1")
            )
            db.conn.commit()
            result["migrations_applied"].append("v0 -> v1: 添加 metadata 表")
            logger.info("迁移完成: v0 -> v1")

    result["current_version"] = target_version
    return result


# ---------------------------------------------------------------------------
# Agent Registry - v1.3: 改为登记 Agent 本地安装路径 + 本地记忆文件
# ---------------------------------------------------------------------------

class AgentRegistry:
    """
    Agent 注册表

    扫描各 Agent 的本地安装目录, 登记其本地记忆文件位置。
    OneDrive/AgentMemory 是融合层, 不是源头。
    """

    # 本地安装路径模板: (agent_id, 子目录名, 记忆文件名)
    # 路径在 __init__ 中动态生成，不硬编码
    _AGENT_SUBDIRS = [
        ("hermes", ".hermes", "memories"),
        ("hermes-appdata", "AppData/Local/hermes", "memories"),
        ("claude", ".claude", None),
        ("codex", ".codex", None),
        ("trae", ".trae-cn", None),
        ("trae-appdata", "AppData/Roaming/Trae", None),
        ("claude-appdata", "AppData/Roaming/Claude", None),
    ]

    def _build_local_patterns(self) -> list:
        """动态构建本机路径（不硬编码用户名）"""
        home = Path.home()
        patterns = []
        for agent_id, subdir, mem_name in self._AGENT_SUBDIRS:
            patterns.append((agent_id, str(home / subdir), mem_name))
        return patterns

    def __init__(self, registry_path: Path = None, root: Path = None):
        """
        初始化注册表

        Parameters
        ----------
        registry_path : Path, optional
            Registry 文件路径（默认 _shared/agent_registry.json）
        root : Path, optional
            OneDrive 根目录（融合层）
        """
        if root is None:
            from safe_io import get_data_root
            root = get_data_root()
        self.root = Path(root)
        if registry_path is None:
            registry_path = self.root / "_shared" / "agent_registry.json"
        self.registry_path = Path(registry_path)
        self._ensure_dir()
        self.agents = self._load()
        # 供测试 monkeypatch 覆盖的实例属性
        self.LOCAL_PATTERNS = self._build_local_patterns()

    def _ensure_dir(self):
        """确保 registry 目录存在"""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        """加载 registry"""
        if not self.registry_path.exists():
            return {}
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("agents", {})
        except (json.JSONDecodeError, IOError):
            return {}

    def _save(self):
        """保存 registry"""
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(
                {"version": "1.3", "agents": self.agents},
                f, ensure_ascii=False, indent=2
            )

    def register(self, agent_id: str, installation_path: str = None,
                 memory_files: list = None, **kwargs) -> dict:
        """
        注册一个 Agent (v1.3 字段已重写)

        Parameters
        ----------
        agent_id : str
            Agent ID
        installation_path : str, optional
            本地安装根目录
        memory_files : list, optional
            本地记忆文件路径列表
        **kwargs
            额外字段: display_name, source_device
        """
        info = {
            "agent_id": agent_id,
            "installation_path": installation_path,
            "memory_files": memory_files or [],
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        info.update({k: v for k, v in kwargs.items() if v is not None})
        self.agents[agent_id] = info
        self._save()
        return info

    def update_last_seen(self, agent_id: str) -> bool:
        """更新 Agent 最后活跃时间"""
        if agent_id not in self.agents:
            return False
        self.agents[agent_id]["last_seen"] = datetime.now(timezone.utc).isoformat()
        self._save()
        return True

    def list_all(self) -> list:
        """列出所有 Agent"""
        return list(self.agents.values())

    def get(self, agent_id: str) -> dict:
        """获取单个 Agent 信息"""
        return self.agents.get(agent_id)

    def scan_local(self) -> list:
        """
        v1.3 重写: 扫描本地安装路径, 自动发现 Agent

        Returns
        -------
        list
            新发现的 Agent ID 列表
        """
        new_agents = []

        for agent_id, install_root, mem_subdir in self.LOCAL_PATTERNS:
            install_path = Path(install_root)
            if not install_path.exists():
                continue

            # 检测本地记忆文件
            memory_files = []
            if mem_subdir:
                # Hermes 风格: 固定子目录
                mem_dir = install_path / mem_subdir
                if mem_dir.exists():
                    for md_file in mem_dir.glob("*.md"):
                        memory_files.append(str(md_file))
            elif agent_id.startswith("trae"):
                # Trae Solo: .trae-cn/memory/user_profile.md + .trae-cn/memory/projects/*/topics.md
                mem_dir = install_path / "memory"
                if mem_dir.exists():
                    user_profile = mem_dir / "user_profile.md"
                    if user_profile.exists():
                        memory_files.append(str(user_profile))
                    for topics_file in mem_dir.glob("projects/*/topics.md"):
                        memory_files.append(str(topics_file))
                    for topics_file in mem_dir.glob("projects/*/*/topics.md"):
                        memory_files.append(str(topics_file))
            elif agent_id.startswith("claude"):
                # Claude: .claude/projects/*/memory/MEMORY.md + .claude/projects/*/*.jsonl
                projects_dir = install_path / "projects"
                if projects_dir.exists():
                    for mem_file in projects_dir.glob("*/memory/MEMORY.md"):
                        memory_files.append(str(mem_file))
                    for mem_file in projects_dir.glob("*/memory/user_profile.md"):
                        memory_files.append(str(mem_file))
                    for jsonl_file in projects_dir.glob("*/*.jsonl"):
                        memory_files.append(str(jsonl_file))
            else:
                # 通用: 自动检测常见记忆文件
                candidates = [
                    "MEMORY.md", "memory.md", "memories.md", "preferences.md",
                    "history.jsonl", "USER.md", "user.md"
                ]
                for c in candidates:
                    p = install_path / c
                    if p.exists():
                        memory_files.append(str(p))

            if not memory_files:
                continue

            memory_files = _filter_agent_memory_files(agent_id, memory_files)
            if not memory_files:
                continue

            # 已存在则更新 last_seen
            if agent_id in self.agents:
                self.agents[agent_id]["last_seen"] = datetime.now(timezone.utc).isoformat()
                self.agents[agent_id]["memory_files"] = memory_files
                self.agents[agent_id]["installation_path"] = install_root
                continue

            # 新发现
            self.register(
                agent_id,
                installation_path=install_root,
                memory_files=memory_files
            )
            new_agents.append(agent_id)

        self._save()
        return new_agents

    def get_agent_memory_files(self, agent_id: str) -> list:
        """
        v1.3 新增: 获取指定 Agent 的所有本地记忆文件路径
        """
        info = self.agents.get(agent_id)
        if not info:
            # 实时扫描
            self.scan_local()
            info = self.agents.get(agent_id)
        if not info:
            return []
        return info.get("memory_files", [])

    def update_local_stats(self) -> dict:
        """
        v1.3 新增: 扫描每个 Agent 的本地记忆文件统计

        Returns
        -------
        dict
            {agent_id: {"file_count": N, "total_entries": M, "last_modified": ts}}
        """
        results = {}
        for agent_id, info in self.agents.items():
            stats = {"file_count": 0, "total_entries": 0, "last_modified": None, "total_size": 0}
            for mem_file in info.get("memory_files", []):
                p = Path(mem_file)
                if not p.exists():
                    continue
                stats["file_count"] += 1
                stats["total_size"] += p.stat().st_size
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
                if not stats["last_modified"] or mtime > stats["last_modified"]:
                    stats["last_modified"] = mtime

                # 用 LocalMemoryParser 估算条目数
                try:
                    parser = LocalMemoryParser()
                    entries = parser.parse_file(p)
                    stats["total_entries"] += len(entries)
                except:
                    pass

            info["local_stats"] = stats
            results[agent_id] = stats

        self._save()
        return results

    def update_memory_counts(self) -> dict:
        """
        v1.3 重写: 统计融合层 (OneDrive/AgentMemory/agent_<id>) 记忆数
        """
        results = {}
        for agent_id, info in self.agents.items():
            # 融合层: root/agent_<id>/memory_private*.md
            agent_dir = self.root / ("agent_" + agent_id)
            private_count = 0
            if agent_dir.exists():
                import re
                for md_file in agent_dir.glob("memory_private*.md"):
                    try:
                        text = md_file.read_text(encoding="utf-8")
                        private_count += len(re.findall(r"^\s*id:\s*mem_", text, re.MULTILINE))
                    except:
                        pass
            shared_count = 0
            shared_file = agent_dir / "memory_shared.md"
            if shared_file.exists():
                try:
                    import re
                    text = shared_file.read_text(encoding="utf-8")
                    shared_count = len(re.findall(r"^\s*id:\s*mem_", text, re.MULTILINE))
                except:
                    pass

            info["fused_memory_count"] = private_count
            info["fused_shared_count"] = shared_count
            results[agent_id] = (private_count, shared_count)

        self._save()
        return results


# ---------------------------------------------------------------------------
# LocalMemoryParser - v1.3 新增: 多格式本地记忆解析
# ---------------------------------------------------------------------------

_SYNC_MARKER_RE = re.compile(r"\[sync:mem_[^\]]+\]", re.IGNORECASE)


def _normalize_memory_content(text: str) -> str:
    """标准化记忆内容，避免换行差异导致重复。"""
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _strip_sync_generated_sections(text: str) -> str:
    """移除写回阶段生成的同步区块，避免再次被提取。"""
    text = _normalize_memory_content(text)
    if not text:
        return ""

    # Trae: 移除整个 Shared Knowledge 段，保留其它用户画像内容
    text = re.sub(
        r"(?ms)^##\s+Shared Knowledge\s*$.*?(?=^##\s+|\Z)",
        "",
        text,
    )

    # Claude: 移除 MEMORY.md 中指向 shared_from_agents.md 的自动索引行
    lines = []
    for line in text.split("\n"):
        if "shared_from_agents.md" in line.lower():
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _is_sync_generated_content(text: str) -> bool:
    """判断内容是否为写回阶段生成的同步产物。"""
    normalized = _normalize_memory_content(text)
    if not normalized:
        return False

    lowered = normalized.lower()
    if _SYNC_MARKER_RE.search(normalized):
        return True
    if "shared_from_agents.md" in lowered:
        return True
    if "name: 来自其他 agent 的共享记忆" in lowered:
        return True
    if "以下记忆由同步工具从其他 agent 自动导入" in normalized:
        return True
    return False


def _should_skip_agent_memory_file(agent_id: str, file_path: Path) -> bool:
    """过滤明显属于同步写回产物的文件。"""
    _ = agent_id  # 预留：后续可按 agent 定制过滤规则
    name = file_path.name.lower()
    if name == "shared_from_agents.md":
        return True
    return False


def _filter_agent_memory_files(agent_id: str, memory_files: list) -> list:
    """过滤并去重 Agent 记忆文件列表。"""
    filtered = []
    seen = set()
    for mem_file in memory_files or []:
        p = Path(mem_file)
        if _should_skip_agent_memory_file(agent_id, p):
            continue
        mem_path = str(p)
        if mem_path in seen:
            continue
        seen.add(mem_path)
        filtered.append(mem_path)
    return filtered


class LocalMemoryParser:
    """
    本地记忆文件解析器

    支持格式:
    - Hermes MEMORY.md: § 分隔
    - Hermes USER.md: 单段文本
    - 通用 .md: 按 front matter 或空行分隔
    - Claude history.jsonl: 提取 user 类型消息
    """

    @staticmethod
    def detect_format(file_path: Path) -> str:
        """
        检测文件格式

        Returns
        -------
        str
            'hermes_section' / 'hermes_user' / 'markdown' / 'jsonl' / 'unknown'
        """
        name = file_path.name.lower()
        if "memory.md" in name and "hermes" in str(file_path).lower():
            return "hermes_section"
        if "user.md" in name and "hermes" in str(file_path).lower():
            return "hermes_user"
        if name.endswith(".jsonl"):
            return "jsonl"
        if name.endswith(".md"):
            return "markdown"
        return "unknown"

    def parse_file(self, file_path: Path) -> list:
        """
        解析文件, 返回记忆条目列表

        Returns
        -------
        list
            [{"content": str, "tags": list, "confidence": str, "source_format": str}, ...]
        """
        file_path = Path(file_path)
        if not file_path.exists():
            return []

        fmt = self.detect_format(file_path)
        if fmt == "hermes_section":
            return self.parse_hermes_memory(file_path)
        elif fmt == "hermes_user":
            return self.parse_hermes_user(file_path)
        elif fmt == "markdown":
            return self.parse_markdown(file_path)
        elif fmt == "jsonl":
            return self.parse_jsonl(file_path)
        return []

    def parse_hermes_memory(self, file_path: Path) -> list:
        """
        解析 Hermes MEMORY.md (用 § 分隔的多条记忆)
        """
        text = file_path.read_text(encoding="utf-8")
        sections = [s.strip() for s in text.split("§") if s.strip()]
        entries = []
        kept_idx = 0
        for section in sections:
            section = _strip_sync_generated_sections(section)
            if not section or _is_sync_generated_content(section):
                continue
            kept_idx += 1
            # 截取前 200 字作为内容预览, 完整内容保留
            first_line = section.split("\n")[0][:60]
            entries.append({
                "content": section,
                "tags": ["从Hermes导入", f"第{kept_idx}条"],
                "confidence": "high",  # Hermes 记忆一般是高置信度
                "source_format": "hermes_section",
                "preview": first_line,
            })
        return entries

    def parse_hermes_user(self, file_path: Path) -> list:
        """
        解析 Hermes USER.md (单段自由文本)
        """
        text = _strip_sync_generated_sections(file_path.read_text(encoding="utf-8").strip())
        if not text or _is_sync_generated_content(text):
            return []
        return [{
            "content": text,
            "tags": ["用户身份", "从Hermes导入"],
            "confidence": "high",
            "source_format": "hermes_user",
            "preview": text.split("\n")[0][:60],
        }]

    def parse_markdown(self, file_path: Path) -> list:
        """
        解析通用 Markdown: 按 front matter 或空行分块
        """
        text = _strip_sync_generated_sections(file_path.read_text(encoding="utf-8"))
        entries = []
        # 先按 front matter 切分
        import re
        blocks = re.split(r"\n---\n", text)
        for block in blocks:
            block = _normalize_memory_content(block)
            if not block:
                continue
            # 跳过纯标题块
            if block.startswith("#") and len(block.split("\n")) == 1:
                continue
            if _is_sync_generated_content(block):
                continue
            entries.append({
                "content": block,
                "tags": ["从本地导入"],
                "confidence": "medium",
                "source_format": "markdown",
                "preview": block.split("\n")[0][:60],
            })
        return entries

    def parse_jsonl(self, file_path: Path) -> list:
        """
        解析 JSONL 记忆文件 (v1.3 多格式兼容)

        支持:
        - Claude history.jsonl v2: {"type": "user", "message": {"content": "..."}}
        - Claude history.jsonl v1: {"display": "prompt", "timestamp": ..., "sessionId": ...}
        """
        entries = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    content = None
                    src_tag = "从JSONL导入"

                    # 格式 v2: {"type": "user", "message": {"content": ...}}
                    if record.get("type") == "user":
                        msg = record.get("message", {})
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                            content = "\n".join(texts)
                        src_tag = "从Claude历史导入"

                    # 格式 v1: {"display": "prompt", "timestamp": ..., "sessionId": ...}
                    elif "display" in record and "timestamp" in record:
                        content = record.get("display", "")
                        src_tag = "从Claude prompt历史导入"

                    if not content or len(content) <= 5:
                        continue

                    content = _strip_sync_generated_sections(content)
                    if not content or _is_sync_generated_content(content):
                        continue

                    entries.append({
                        "content": content,
                        "tags": [src_tag],
                        "confidence": "low",
                        "source_format": "jsonl",
                        "preview": content[:60],
                    })
        except (IOError, UnicodeDecodeError):
            pass
        return entries


# ---------------------------------------------------------------------------
# Extractor - v1.3 新增: 从本地记忆文件提取到 OneDrive 融合层
# ---------------------------------------------------------------------------

def extract_local_to_fused(agent_id: str, root: Path = None,
                            local_files: list = None,
                            registry: AgentRegistry = None,
                            on_progress: Callable[[str], None] = None) -> dict:
    """从 Agent 的本地记忆文件提取到 OneDrive 融合层

    v1.3.2 改进：
    - 累积 50 条一批批量 INSERT（避免 417 条 × 4 agent 单条 commit 导致 disk I/O）
    - 每批 emit 进度，消除"卡死 648 秒"的错觉
    """
    if on_progress is None:
        on_progress = lambda m: None
    if root is None:
        from safe_io import get_data_root
        root = get_data_root()
    root = Path(root)

    if registry is None:
        registry = AgentRegistry(root=root)

    if local_files is None:
        local_files = registry.get_agent_memory_files(agent_id)
    local_files = _filter_agent_memory_files(agent_id, local_files)

    parser = LocalMemoryParser()
    result = {
        "agent_id": agent_id,
        "extracted": 0,
        "skipped": 0,
        "files": [],
        "errors": [],
    }

    if not local_files:
        result["errors"].append("未找到本地记忆文件, 请先运行 discover")
        return result

    # 准备融合层目录
    fused_agent_dir = root / ("agent_" + agent_id)
    fused_agent_dir.mkdir(parents=True, exist_ok=True)

    # 收集所有解析的条目
    all_entries = []
    for local_file in local_files:
        local_file = Path(local_file)
        if not local_file.exists():
            result["skipped"] += 1
            continue
        try:
            entries = parser.parse_file(local_file)
            for entry in entries:
                entry["_source_file"] = str(local_file)
            all_entries.extend(entries)
            result["files"].append(str(local_file))
        except Exception as e:
            result["errors"].append("解析 {} 失败: {}".format(local_file, e))

    if not all_entries:
        return result

    # 确保融合层目录有 identity.json 和 device_config.json（write_memory 需要）
    # 始终覆盖，确保路径正确（旧文件可能含硬编码路径）
    identity_path = fused_agent_dir / "identity.json"
    device_config_path = fused_agent_dir / "device_config.json"
    identity_data = {
        "agent_id": agent_id,
        "display_name": agent_id,
        "primary_domain": "general",
        "memory_root": str(fused_agent_dir),
        "shared_root": str(root / "_shared"),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    identity_path.write_text(json.dumps(identity_data, indent=2, ensure_ascii=False), encoding="utf-8")
    if not device_config_path.exists():
        device_config_path.write_text(
            json.dumps({"source_device": "extracted"}, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    # 确保 shared 目录有必要的文件
    shared_dir = root / "_shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    if not (shared_dir / "agent_runtime_manual.md").exists():
        (shared_dir / "agent_runtime_manual.md").write_text(
            "# Agent 运行手册\n\n## 启动流程\n1. 读取 identity.json\n2. 加载记忆\n",
            encoding="utf-8"
        )
    if not (shared_dir / "writing_policy.md").exists():
        (shared_dir / "writing_policy.md").write_text(
            "# 记忆写入约束\n\n## 规则\n- 每条记忆必须有标签\n- confidence 必须是 high/medium/low\n",
            encoding="utf-8"
        )

    # 确保 agent 目录有 last_sync.json
    last_sync_path = fused_agent_dir / "last_sync.json"
    if not last_sync_path.exists():
        sync_data = {
            "agent_id": agent_id,
            "last_merge_timestamp": None,
            "last_merge_id": None,
            "shared_memory_version": "v0"
        }
        last_sync_path.write_text(json.dumps(sync_data, indent=2), encoding="utf-8")

    # 确保 memory_private.md 存在
    private_md = fused_agent_dir / "memory_private.md"
    if not private_md.exists():
        private_md.write_text("# {} 私有记忆\n".format(agent_id), encoding="utf-8")
    shared_md = fused_agent_dir / "memory_shared.md"
    if not shared_md.exists():
        shared_md.write_text("# {} 共享记忆\n".format(agent_id), encoding="utf-8")

    # 启动上下文（如未启动）
    global _context
    _context = None
    startup(identity_path, device_config_path)

    trigger = TriggerEngine(root=root)
    private_md = fused_agent_dir / "memory_private.md"
    existing_content = read_file_if_exists(private_md)
    db_path = fused_agent_dir / "memories.db"

    # 获取当前最大序号（用于批量生成 ID）
    date_str = now_YYYYMMDD()
    prefix = "mem_{}_extracted_".format(date_str)
    current_max_seq = 0
    for md_file in fused_agent_dir.glob("memory_private*.md"):
        if md_file.suffix == ".md" and not md_file.name.endswith(".lock") and not md_file.name.endswith(".tmp"):
            current_max_seq = max(current_max_seq, _scan_max_sequence(md_file, prefix))

    # v1.3.2: 批量写入 + 实时进度回调
    BATCH_SIZE = 50
    entries_buffer = []  # 待批量插入的 MemoryEntry 列表
    seen_hashes = set()

    def _flush_batch():
        nonlocal existing_content
        if entries_buffer:
            n = 0
            try:
                with MemoryDatabase(db_path) as batch_db:
                    n = batch_db.insert_memories_batch(entries_buffer)
            except Exception:
                pass
            # 已成功插入到 SQLite 的条目，追加到 .md 缓存
            for mem in entries_buffer:
                existing_content = append_memory_entry(existing_content, mem)
            entries_buffer.clear()
            return n
        return 0

    def _emit_progress():
        processed = result["extracted"] + result["skipped"]
        total = len(all_entries)
        on_progress("  [{}] {}/{} (已提取={} 跳过={})".format(
            agent_id, processed, total, result["extracted"], result["skipped"]
        ))

    for idx, entry in enumerate(all_entries, 1):
        content = entry.get("content", "").strip()
        if not content:
            result["skipped"] += 1
            continue

        tags = entry.get("tags", [])
        if not tags:
            tags = ["从本地导入"]

        # 过滤含 HTML 的内容
        if re.search(r"<[a-zA-Z/][^>]*>", content):
            result["skipped"] += 1
            continue

        # v1.3.2: 单条记忆上限，防止超大文本把 SQLite / OneDrive 撑爆
        MAX_CONTENT_LEN = 32 * 1024
        if len(content) > MAX_CONTENT_LEN:
            content = content[:MAX_CONTENT_LEN] + "\n\n[TRUNCATED: original content was too large for stable OneDrive sync]"
            if "超长记忆" not in tags:
                tags.append("超长记忆")

        # 过滤太短的内容
        if len(content) < 10:
            result["skipped"] += 1
            continue

        src_format = entry.get("source_format", "unknown")
        if "从{}导入".format(src_format) not in tags:
            tags.append("从{}导入".format(src_format))

        confidence = entry.get("confidence", "medium")

        # 触发器检测
        trigger_result = trigger.match_hotword(content)
        if trigger_result["matched"]:
            confidence = trigger_result["force_confidence"]
            for tag in trigger_result["auto_tags"]:
                if tag not in tags:
                    tags.append(tag)

        # 敏感信息检测
        detector = get_detector()
        sensitive_result = detector.check(content)
        if sensitive_result["blocked"]:
            result["skipped"] += 1
            continue

        # 生成条序号（内存递增，不扫描文件）
        current_max_seq += 1
        if current_max_seq > 999:
            break
        memory_id = "{}{:03d}".format(prefix, current_max_seq)

        timestamp = datetime.now(timezone.utc).isoformat()
        mem_entry = MemoryEntry(
            id=memory_id,
            agent_id=agent_id,
            timestamp=timestamp,
            source_device="extracted",
            domain="general",
            tags=tags,
            confidence=confidence,
            conflict_with=None,
            content=content,
        )

        normalized_content = _normalize_memory_content(content)
        content_sig = content_hash(normalized_content)
        is_dup_in_db = False
        try:
            with MemoryDatabase(db_path) as check_db:
                cursor = check_db.conn.execute(
                    "SELECT 1 FROM memories WHERE substr(id, 1, 4) = 'mem_' AND content = ? LIMIT 1",
                    (normalized_content,)
                )
                is_dup_in_db = cursor.fetchone() is not None
        except Exception:
            is_dup_in_db = False

        if is_dup_in_db or content_sig in seen_hashes:
            result["skipped"] += 1
            continue

        seen_hashes.add(content_sig)
        entries_buffer.append(mem_entry)
        result["extracted"] += 1

        # 达到批量大小则 flush
        if len(entries_buffer) >= BATCH_SIZE:
            _flush_batch()
            _emit_progress()

    # 处理最后不足一批的条目
    _flush_batch()
    if result["extracted"]:
        _emit_progress()

    # 一次性写入文件（使用安全写回，绕过 Agent/杀软文件锁）
    if not _safe_write_text(private_md, existing_content, encoding="utf-8"):
        result["errors"].append("写入融合层文件失败（可能被锁定）: {}".format(private_md))
    result["fused_file"] = str(private_md)
    return result


# ---------------------------------------------------------------------------
# Hotword Trigger Engine - 关键词触发器
# ---------------------------------------------------------------------------

class TriggerEngine:
    """
    关键词触发器引擎

    当写入内容包含触发词时,自动调整 confidence、追加标签。
    配置存于 _shared/triggers.yaml。
    """

    DEFAULT_TRIGGERS = {
        "chinese": [
            "记住", "以后", "偏好", "不要", "习惯",
            "总是", "千万别", "以后都", "注意", "重要",
        ],
        "english": [
            "remember", "note that", "from now on", "preference",
            "always", "never", "important", "keep in mind",
        ],
        "auto_tags": ["用户明确指令"],
    }

    def __init__(self, triggers_path: Path = None, root: Path = None):
        """
        初始化触发器引擎

        Parameters
        ----------
        triggers_path : Path, optional
            triggers.yaml 路径
        root : Path, optional
            OneDrive 根目录
        """
        if root is None:
            from safe_io import get_data_root
            root = get_data_root()
        self.root = Path(root)
        if triggers_path is None:
            triggers_path = self.root / "_shared" / "triggers.yaml"
        self.triggers_path = Path(triggers_path)
        self.config = self._load()

    def _load(self) -> dict:
        """加载触发器配置"""
        if not self.triggers_path.exists():
            return {
                "enabled": True,
                "chinese": self.DEFAULT_TRIGGERS["chinese"],
                "english": self.DEFAULT_TRIGGERS["english"],
                "auto_tags": self.DEFAULT_TRIGGERS["auto_tags"],
                "force_confidence": "high",
            }

        try:
            text = self.triggers_path.read_text(encoding="utf-8")
            return self._parse_yaml(text)
        except (IOError, UnicodeDecodeError):
            return {"enabled": True, "chinese": [], "english": [], "auto_tags": []}

    def _parse_yaml(self, text: str) -> dict:
        """解析 YAML，优先用 pyyaml，fallback 到简单解析"""
        result = {"enabled": True, "chinese": [], "english": [], "auto_tags": []}

        # 优先使用 pyyaml
        try:
            import yaml
            parsed = yaml.safe_load(text)
            if isinstance(parsed, dict):
                for key in result:
                    if key in parsed:
                        result[key] = parsed[key]
                return result
        except ImportError:
            pass
        except Exception:
            pass

        # fallback: 简单解析
        return self._parse_simple_yaml(text)

    def _parse_simple_yaml(self, text: str) -> dict:
        """极简 YAML 解析（pyyaml 不可用时的 fallback）"""
        result = {"enabled": True, "chinese": [], "english": [], "auto_tags": []}
        current_list_key = None
        for line in text.split("\n"):
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("  - "):
                if current_list_key:
                    value = line[4:].strip().strip('"').strip("'")
                    if isinstance(result.get(current_list_key), list):
                        result[current_list_key].append(value)
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if value == "":
                    current_list_key = key
                    if key not in result:
                        result[key] = []
                elif value.lower() in ("true", "false"):
                    result[key] = (value.lower() == "true")
                else:
                    result[key] = value.strip('"').strip("'")
                    current_list_key = None
        return result

    def save(self):
        """保存当前配置回 yaml"""
        lines = ["# Triggers Configuration", "# 用户偏好: 直接编辑此文件即可调整触发词", ""]
        lines.append("enabled: {}".format("true" if self.config.get("enabled", True) else "false"))
        lines.append("")
        for key in ["chinese", "english", "auto_tags"]:
            lines.append("{}:".format(key))
            items = self.config.get(key, [])
            if not isinstance(items, list):
                items = [items]
            for item in items:
                lines.append("  - \"{}\"".format(item))
            lines.append("")
        self.triggers_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.triggers_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def match_hotword(self, content: str) -> dict:
        """
        检测内容是否命中触发词

        Returns
        -------
        dict
            {"matched": bool, "matched_words": [str], "auto_tags": [str],
             "force_confidence": str or None}
        """
        if not self.config.get("enabled", True):
            return {"matched": False, "matched_words": [], "auto_tags": [], "force_confidence": None}

        if not content:
            return {"matched": False, "matched_words": [], "auto_tags": [], "force_confidence": None}

        content_lower = content.lower()
        matched = []

        for word in self.config.get("chinese", []):
            if word and word in content:
                matched.append(word)

        for word in self.config.get("english", []):
            if word and word.lower() in content_lower:
                matched.append(word)

        if not matched:
            return {"matched": False, "matched_words": [], "auto_tags": [], "force_confidence": None}

        return {
            "matched": True,
            "matched_words": matched,
            "auto_tags": list(self.config.get("auto_tags", ["用户明确指令"])),
            "force_confidence": self.config.get("force_confidence", "high"),
        }


# ---------------------------------------------------------------------------
# Session Flusher - 会话落盘钩子
# ---------------------------------------------------------------------------

class SessionFlusher:
    """
    会话落盘钩子

    Agent 在对话过程中把"待落盘"条目暂存到 memory_pending_buffer.json,
    在对话结束 / safe-quit 时调用 flush() 批量写入。
    """

    def __init__(self, memory_root: Path, buffer_path: Path = None):
        """
        初始化 flusher

        Parameters
        ----------
        memory_root : Path
            记忆根目录（用于默认 buffer 路径）
        buffer_path : Path, optional
            buffer 文件路径（默认 memory_pending_buffer.json）
        """
        self.memory_root = Path(memory_root)
        if buffer_path is None:
            buffer_path = self.memory_root / "memory_pending_buffer.json"
        self.buffer_path = Path(buffer_path)

    def add_pending(self, content: str, tags: list = None,
                    confidence: str = "medium", domain: str = None) -> int:
        """
        添加待落盘条目

        Returns
        -------
        int
            当前 buffer 中条目数
        """
        entry = {
            "content": content,
            "tags": tags or [],
            "confidence": confidence,
            "domain": domain,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }

        buffer = self._load_buffer()
        buffer.append(entry)
        self._save_buffer(buffer)
        return len(buffer)

    def _load_buffer(self) -> list:
        if not self.buffer_path.exists():
            return []
        try:
            with open(self.buffer_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def _save_buffer(self, buffer: list):
        with open(self.buffer_path, "w", encoding="utf-8") as f:
            json.dump(buffer, f, ensure_ascii=False, indent=2)

    def clear_buffer(self):
        """清空 buffer"""
        if self.buffer_path.exists():
            self.buffer_path.unlink()

    def filter_by_policy(self, entry: dict) -> bool:
        """
        按 writing_policy.md 过滤

        简单规则:
        - 太短(< 10 字符) → 过滤
        - 包含闲聊关键词 → 过滤
        """
        content = entry.get("content", "").strip()
        if not content:
            return False
        if len(content) < 10:
            return False
        # 闲聊关键词
        chitchat = ["你好", "再见", "哈哈", "呵呵", "ok", "好的", "thanks", "thank you"]
        content_lower = content.lower()
        if content_lower.strip() in [c.lower() for c in chitchat]:
            return False
        return True

    def flush(self, dry_run: bool = False) -> dict:
        """
        批量落盘 buffer 中的所有条目

        Returns
        -------
        dict
            {"total": N, "written": N, "skipped": N, "skipped_entries": [...]}
        """
        buffer = self._load_buffer()
        result = {
            "total": len(buffer),
            "written": 0,
            "skipped": 0,
            "skipped_entries": [],
        }

        if not buffer:
            return result

        # 去重：按内容去重，保留首次出现的条目
        seen_contents = set()
        deduped_buffer = []
        for entry in buffer:
            content_key = entry.get("content", "").strip()
            if content_key and content_key not in seen_contents:
                seen_contents.add(content_key)
                deduped_buffer.append(entry)
            else:
                result["skipped"] += 1
                result["skipped_entries"].append({
                    "content": content_key[:50],
                    "reason": "duplicate_in_buffer",
                })
        buffer = deduped_buffer

        # 自动启动 Agent (如未启动)
        if _context is None:
            identity_path = self.memory_root / "identity.json"
            device_config_path = self.memory_root / "device_config.json"
            if not device_config_path.exists():
                # 兜底: 使用本地代码目录或自动创建
                local_dc = Path(__file__).parent / "device_config.json"
                if local_dc.exists():
                    device_config_path = local_dc
                else:
                    import socket
                    default_device = socket.gethostname().lower().replace("\\", "_").replace(" ", "_")
                    device_config_path.write_text(
                        json.dumps({"source_device": default_device}, indent=2, ensure_ascii=False),
                        encoding="utf-8"
                    )
            startup(identity_path, device_config_path)

        for entry in buffer:
            if not self.filter_by_policy(entry):
                result["skipped"] += 1
                result["skipped_entries"].append({
                    "content": entry.get("content", "")[:50],
                    "reason": "policy_filtered",
                })
                continue

            if not dry_run:
                try:
                    write_memory(
                        content=entry["content"],
                        tags=entry.get("tags", []),
                        confidence=entry.get("confidence", "medium"),
                        domain=entry.get("domain"),
                    )
                    result["written"] += 1
                except Exception as e:
                    result["skipped"] += 1
                    result["skipped_entries"].append({
                        "content": entry.get("content", "")[:50],
                        "reason": str(e)[:100],
                    })
            else:
                result["written"] += 1

        if not dry_run:
            # 同步到 SQLite
            try:
                sync_markdown_to_db()
            except Exception:
                pass
            self.clear_buffer()

        return result