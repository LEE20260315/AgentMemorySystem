"""Safe file I/O utilities with retry and atomic write support."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 数据根注册点（v2.1.1，根治数据分裂）
#
# 原理：数据根（Data Root）的唯一事实来源是一个**持久化注册文件**：
#   %LOCALAPPDATA%\AgentMemorySystem\data_root.txt
#
# 任何进程（GUI / CLI / watchdog / 开发模式 / 任意入口）启动时，
# get_data_root() 都先读这个注册文件；只有首次运行（注册文件不存在）
# 才做一次路径推导并写入注册文件。
#
# 由此根治分裂：不再存在"每个进程各自推导路径"的多级 fallback 链，
# 全机所有进程永远解析到同一个数据根。
# ---------------------------------------------------------------------------
_REGISTRY_FILE = "data_root.txt"
_registry_path: Optional[Path] = None  # 进程内缓存，避免每次磁盘读
_last_registry_warn: float = 0.0


def _registry_file() -> Path:
    """注册文件路径（LOCALAPPDATA 下，本机稳定、不随项目移动）。"""
    global _registry_path
    if _registry_path is None:
        local_appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        base = local_appdata / "AgentMemorySystem"
        _registry_path = base / _REGISTRY_FILE
    return _registry_path


def _read_registry() -> Optional[Path]:
    """读取注册点。返回 None 表示尚未注册（首次运行）。"""
    try:
        reg = _registry_file()
        if reg.exists():
            text = reg.read_text(encoding="utf-8").strip()
            if text:
                p = Path(text).expanduser()
                # 注册路径必须有效（目录存在且可写）才采用
                if p.is_dir():
                    return p
    except OSError:
        pass
    return None


def _write_registry(root: Path) -> None:
    """把数据根写入注册点（原子写）。"""
    try:
        reg = _registry_file()
        reg.parent.mkdir(parents=True, exist_ok=True)
        tmp = reg.with_suffix(".tmp")
        tmp.write_text(str(root), encoding="utf-8")
        tmp.replace(reg)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 候选推导（仅首次运行 / 注册失效时使用）
# ---------------------------------------------------------------------------
def _candidate_project_data() -> Optional[Path]:
    """候选1：项目根目录下的 AgentMemory/（含 OneDrive 跨设备同步）。"""
    if getattr(sys, "frozen", False):
        # PyInstaller 下 __file__ 指向 _internal/，从 EXE 位置向上回溯
        exe_dir = Path(sys.executable).resolve().parent
        for candidate_root in (exe_dir, exe_dir.parent, exe_dir.parent.parent):
            cand = candidate_root / "AgentMemory"
            if cand.is_dir() and (cand / "sync_settings.json").exists():
                return cand
        return None
    # 开发模式：脚本所在目录
    project_data = Path(__file__).resolve().parent / "AgentMemory"
    if project_data.is_dir() and (project_data / "sync_settings.json").exists():
        return project_data
    return None


def _candidate_env() -> Optional[Path]:
    """候选2：环境变量 AGENT_MEMORY_DATA_DIR（BAT 启动器注入）。"""
    env_data = os.environ.get("AGENT_MEMORY_DATA_DIR")
    if not env_data:
        return None
    p = Path(env_data).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except OSError:
        return None


def _candidate_legacy() -> Optional[Path]:
    """候选3：历史 data/ 目录（v2.0.x 时代引擎数据根，仅迁移读取）。"""
    if getattr(sys, "frozen", False):
        return None
    legacy = Path(__file__).resolve().parent / "data"
    if legacy.is_dir():
        return legacy
    return None


def _candidate_local_appdata() -> Path:
    """候选4：LOCALAPPDATA 兜底（本机标准位置，最后手段）。"""
    local_appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    fallback = local_appdata / "AgentMemorySystem"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _resolve_and_register() -> Path:
    """首次运行：按优先级推导数据根并写入注册点。

    优先级：环境变量（BAT 显式指定）> 项目根 AgentMemory/ > 历史 data/ > LOCALAPPDATA。
    注意：项目根 AgentMemory/ 有 sync_settings.json 特征（数据已在其中），
    若目录已存在且含同步数据则优先于 env（env 可能指向空的新位置）。
    """
    # 0) 已有注册点且有效 → 直接采用（单一事实来源）
    registered = _read_registry()
    if registered is not None:
        return registered

    # 1) 项目根 AgentMemory/（若已存在数据，这是最强的证据）
    project = _candidate_project_data()
    if project is not None and (project / "sync_settings.json").exists():
        _write_registry(project)
        return project

    # 2) 环境变量（BAT 启动器显式指定）
    env = _candidate_env()
    if env is not None:
        _write_registry(env)
        return env

    # 3) 历史 data/（迁移兼容）
    legacy = _candidate_legacy()
    if legacy is not None:
        # data/ 存在时检查是否含实际数据
        has_data = any(legacy.iterdir()) if legacy.exists() else False
        if has_data:
            _write_registry(legacy)
            return legacy

    # 4) LOCALAPPDATA 兜底
    fallback = _candidate_local_appdata()
    _write_registry(fallback)
    return fallback


def get_local_data_dir() -> Path:
    """获取本机私有数据目录（LOCALAPPDATA/AgentMemorySystem）。

    v2.2.0 引入：跨机共享数据（memory_*.md、device_config.json 等）放数据根
    （get_data_root()，通常为 OneDrive）；本机私有数据（SQLite 缓存、日志）
    必须放这里，避免 SQLite 在 OneDrive 双向同步下损坏/冲突。

    Returns
    -------
    Path
        本机私有数据目录（已确保存在）
    """
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "AgentMemorySystem"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def get_data_root() -> Path:
    """获取数据根目录（唯一事实来源，根治数据分裂）。

    v2.1.1 根治方案：
    - 数据根由持久化注册文件 %LOCALAPPDATA%\\AgentMemorySystem\\data_root.txt 唯一决定
    - 所有入口（GUI / CLI / watchdog / 开发模式 / 任意 EXE 副本）调用本函数
      时都只读注册点；仅首次运行才做路径推导并落盘
    - 注册点失效（目录被删除/移动）时自动重新推导并更新注册点，
      保证永不静默分裂

    Returns
    -------
    Path
        数据根目录路径（已确保存在）
    """
    # 进程内缓存（同一进程内路径不会变，避免反复磁盘读）
    # 注意：env 变化（测试/迁移场景）时缓存可能失效，需校验
    cached = getattr(get_data_root, "_cached", None)
    env_data = os.environ.get("AGENT_MEMORY_DATA_DIR")
    if cached is not None and cached.is_dir():
        if env_data:
            env_path_cached = Path(env_data).expanduser()
            if str(cached).lower() == str(env_path_cached).lower():
                return cached
            # env 与缓存不一致：环境变量优先级更高，忽略缓存
        else:
            return cached

    # 0) 环境变量（最高权威：BAT 每次启动都显式指定，用于纠正注册点）
    if env_data:
        env_path = Path(env_data).expanduser()
        try:
            env_path.mkdir(parents=True, exist_ok=True)
            test = env_path / ".writable_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            # 同步注册点：BAT 指定即权威，避免被历史错误注册带偏
            registered = _read_registry()
            if registered != env_path:
                _write_registry(env_path)
            get_data_root._cached = env_path
            return env_path
        except OSError:
            pass  # env 不可写，继续走注册点

    # 1) 注册点（单一事实来源，供无 env 的进程：watchdog / 直接双击）
    registered = _read_registry()
    if registered is not None:
        try:
            registered.mkdir(parents=True, exist_ok=True)
            test = registered / ".writable_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            get_data_root._cached = registered
            return registered
        except OSError:
            # 注册路径不可写（目录被删/权限变化）→ 重新推导
            pass

    # 2) 首次运行或注册失效 → 推导 + 注册
    root = _resolve_and_register()
    get_data_root._cached = root
    return root


def get_data_root_no_cache() -> Path:
    """强制重新解析（测试 / 迁移后用）。"""
    if hasattr(get_data_root, "_cached"):
        del get_data_root._cached
    return get_data_root()


def reset_data_root_cache() -> None:
    """清空进程内缓存（供测试）。"""
    if hasattr(get_data_root, "_cached"):
        del get_data_root._cached


def set_data_root_override(path) -> Optional[Path]:
    """把数据根重定向到指定位置（用于测试隔离 / 用户迁移）。

    会写入注册点，使所有后续进程都指向新位置。
    返回之前的注册值（便于恢复）。
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    old = _read_registry()
    _write_registry(p)
    reset_data_root_cache()
    return old


def _pending_path(target: Path) -> Path:
    """Return the .pending path for a target file (used for locked-file fallback)."""
    return target.with_suffix(target.suffix + ".pending")


def _safe_write_text(path, content: str, encoding: str = "utf-8", retries: int = 3) -> bool:
    """Write text to file with retry on PermissionError.

    Uses atomic write (tmp + rename) when possible, falls back to direct write.
    Returns True on success.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")

    for attempt in range(retries):
        try:
            with open(tmp, "w", encoding=encoding) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            try:
                tmp.replace(path)
            except OSError:
                # Target locked (OneDrive / another process) → direct write
                with open(path, "w", encoding=encoding) as f:
                    f.write(content)
            return True
        except PermissionError:
            # File locked by another process → write to .pending
            try:
                pending = _pending_path(path)
                with open(pending, "w", encoding=encoding) as f:
                    f.write(content)
                return True
            except OSError:
                pass
        except OSError:
            pass
        if attempt < retries - 1:
            time.sleep(0.3 * (attempt + 1))
    return False


def _safe_read_text(path, default: str = "", encoding: str = "utf-8", max_size: int = 50 * 1024 * 1024) -> str:
    """Read text from file with retry on PermissionError.

    Returns default if file doesn't exist or can't be read.
    Files larger than max_size (default 50MB) are truncated to avoid MemoryError.
    """
    path = Path(path)
    if not path.exists():
        return default

    for attempt in range(3):
        try:
            # 检查文件大小，超大文件截断读取避免 MemoryError
            try:
                file_size = path.stat().st_size
            except OSError:
                file_size = 0

            if file_size > max_size:
                # 只读取最后 max_size 字节
                with open(path, "r", encoding=encoding, errors="replace") as f:
                    f.seek(max(0, file_size - max_size))
                    return f.read()
            else:
                with open(path, "r", encoding=encoding, errors="replace") as f:
                    return f.read()
        except PermissionError:
            # Try reading .pending file
            try:
                pending = _pending_path(path)
                if pending.exists():
                    with open(pending, "r", encoding=encoding, errors="replace") as f:
                        return f.read()
            except OSError:
                pass
        except MemoryError:
            # v2.1.2: 超大文件即使截断读取仍内存不足：直接返回默认值（重试无意义）
            return default
        except OSError:
            # 其他 OSError：重试（最多 3 次），最终返回默认值
            pass
        if attempt < 2:
            time.sleep(0.3 * (attempt + 1))
    return default
