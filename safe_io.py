"""Safe file I/O utilities with retry and atomic write support."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 用户主目录权威解析（v2.2.0，根治跨机路径错位）
# ---------------------------------------------------------------------------
#
# 背景：某些机器的 USERPROFILE / LOCALAPPDATA / HOME 环境变量会残留指向
# 旧账户（如 C:\Users\MR.Dong），导致 expanduser / Path.home() 解析到他人
# 家目录，提取/写回被拒（WinError 5）。
#
# 根治：优先用 SHGetKnownFolderPath(FOLDERID_Profile)——它按**当前进程 token**
# 查真实登录用户目录，不读任何环境变量，跨机残留场景依然返回正确结果。
#
if sys.platform == "win32":
    import ctypes

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("d1", ctypes.c_uint),
            ("d2", ctypes.c_ushort),
            ("d3", ctypes.c_ushort),
            ("d4", ctypes.c_ubyte * 8),
        ]

    # FOLDERID_Profile = {5E6C858F-0E22-4760-9AFE-EA3317B67173}
    _FOLDERID_PROFILE = _GUID(
        0x5E6C858F, 0x0E22, 0x4760,
        (ctypes.c_ubyte * 8)(0x9A, 0xFE, 0xEA, 0x33, 0x17, 0xB6, 0x71, 0x73),
    )

    def _known_folder_profile() -> Optional[Path]:
        """SHGetKnownFolderPath(FOLDERID_Profile)：按进程 token 查真实用户目录。"""
        try:
            ptr = ctypes.c_wchar_p()
            ok = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(_FOLDERID_PROFILE), 0, None, ctypes.byref(ptr)
            )
            if ok == 0 and ptr.value:
                return Path(ptr.value)
        except Exception:
            pass
        return None
else:
    def _known_folder_profile() -> Optional[Path]:
        return None

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


def get_local_home() -> Path:
    """跨机稳定的用户主目录解析（v2.2.0）。

    问题背景：某些机器 USERPROFILE / Path.home() / expanduser 返回的路径
    与实际登录用户不一致（环境变量残留、旧账户目录并存），导致跨机运行
    时提取/写回路径指向他人家目录（WinError 5 拒绝访问）。

    优先级（从权威到兜底）：
    1. SHGetKnownFolderPath(FOLDERID_Profile)：按进程 token 查真实用户目录，
       不读任何环境变量（跨机残留的根治方案）
    2. LOCALAPPDATA 标准形态推断（C:/Users/<user>/AppData/Local → C:/Users/<user>）
    3. Path.home()（存在时）
    4. USERPROFILE / HOME 环境变量（存在时）
    5. Path.home() 兜底
    """
    # 1) SHGetKnownFolderPath（Windows 权威，进程 token，不受环境变量污染）
    kfp = _known_folder_profile()
    if kfp is not None:
        try:
            if kfp.exists():
                return kfp
        except Exception:
            pass
    # 2) LOCALAPPDATA 推断（与数据根注册点同源，跨机最可信）
    la = os.environ.get("LOCALAPPDATA")
    if la:
        try:
            p = Path(la)
            parts = p.parts
            # 标准形态: <root>/<user>/AppData/Local（最后两层固定 AppData/Local）
            if len(parts) >= 4 and parts[-1] == "Local" and parts[-2] == "AppData":
                cand = p.parents[1]  # 去掉 AppData/Local 两层 → 用户目录
                if cand.exists():
                    return cand
        except Exception:
            pass
    # 3) Path.home()
    try:
        h = Path.home()
        if h.exists():
            return h
    except Exception:
        pass
    # 4) 环境变量
    for var in ("USERPROFILE", "HOME", "HOMEPATH"):
        val = os.environ.get(var)
        if val:
            try:
                p = Path(val)
                if p.exists():
                    return p
            except Exception:
                pass
    # 5) 兜底
    return Path.home()


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
            # v2.2.1: 去掉 .writable_test 同步写测试——数据根在 OneDrive 云同步
            # 目录时，OneDrive 的瞬时文件锁会让 write_text 抛 PermissionError
            # 甚至无限阻塞（v2.2.0 事故：同步/托盘/CLI 全挂）。可写性由实际
            # 写入路径的错误处理兜底，启动路径不再触碰数据根写入。
            env_path.mkdir(parents=True, exist_ok=True)
            # 同步注册点：BAT 指定即权威，避免被历史错误注册带偏
            registered = _read_registry()
            if registered != env_path:
                _write_registry(env_path)
            get_data_root._cached = env_path
            return env_path
        except OSError:
            pass  # env 不可写，继续走注册点

    # 1) 注册点（单一事实来源，供无 env 的进程：watchdog / 直接双击）
    # v2.2.1: 只做只读校验（is_dir），不再 mkdir + 写测试（理由同上：
    # 启动路径绝不触碰 OneDrive 写入）。目录不存在 → 走重新推导。
    registered = _read_registry()
    if registered is not None and registered.is_dir():
        get_data_root._cached = registered
        return registered

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


def _tmp_path(target: Path) -> Path:
    """Return a per-process unique tmp path for atomic writes.

    v2.2.2: 旧实现用 with_suffix(".tmp")（固定名），GUI / watchdog / CLI
    并发写同一目标时会互相覆盖 tmp 并撞 replace；改为"原名 + .tmp + pid"，
    同目录（保证 rename 原子性）且进程间互不冲突。
    """
    return target.with_name(target.name + ".tmp{}".format(os.getpid()))


def _mtime(path: Path) -> float:
    """Safe mtime（不存在返回 0）。"""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def merge_pending_file(target, remove_only: bool = False) -> bool:
    """把 target 的 .pending 快照合并回主文件（启动恢复用）。

    v2.2.2 语义修正：.pending 一律是 _safe_write_text 落下的**完整快照**，
    而非增量片段——所以恢复动作是"新则替换，旧则丢弃"，绝不 append
    （旧版 recover_pending 的 append 会把整份快照再拼一遍 → 内容翻倍）。

    Returns True if a pending file was consumed.
    """
    target = Path(target)
    pending = _pending_path(target)
    if not pending.exists():
        return False
    if remove_only:
        try:
            pending.unlink()
        except OSError:
            pass
        return True
    try:
        if not target.exists() or _mtime(pending) >= _mtime(target):
            os.replace(str(pending), str(target))
        else:
            pending.unlink()  # 主文件更新（如 OneDrive 对端写入）→ 丢弃过期快照
        return True
    except OSError:
        return False


def merge_pending_files(root) -> int:
    """扫描 root 下所有 *.pending 并逐一恢复；顺带清理遗留 *.tmp*。

    在各入口（GUI / CLI / watchdog）启动时调用一次：
    - .pending 比 target 新 → 原子替换回主文件
    - .pending 比 target 旧 → 删除（已被更新的内容取代）
    - 孤儿 .tmp*（崩溃残留）→ 删除
    Returns number of pending files consumed.
    """
    root = Path(root)
    if not root.is_dir():
        return 0
    consumed = 0
    try:
        for p in root.rglob("*.pending"):
            if p.is_file() and merge_pending_file(p.with_suffix("")):
                consumed += 1
        # 崩溃/断电遗留的临时文件：超过 1 小时的视为孤儿，清除
        cutoff = time.time() - 3600
        for p in root.rglob("*.tmp*"):
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                continue
    except OSError:
        pass
    return consumed


def _safe_write_text(path, content: str, encoding: str = "utf-8", retries: int = 5) -> bool:
    """Write text to file with retry — ALWAYS atomic (tmp + rename), never in-place.

    v2.2.2 规则（OneDrive 冲突副本根治）：
    1. 永远先写同目录唯一 tmp（fsync 落盘），再 os.replace 到目标——
       绝不打开目标文件做 in-place 截断重写（旧版在 replace 失败时回退
       direct write，正是 OneDrive"无法操作/建立冲突副本"的元凶：
       云端正在同步的文件被就地改写，同步器无法 reconcile）。
    2. replace 遇 PermissionError（OneDrive / 杀软瞬时锁）→ 指数退避重试。
    3. 持续锁定 → 把完整快照落到 .pending 并在成功后由
       merge_pending_file / merge_pending_files 收编；读取端
       (_safe_read_text) 会优先采用更新的 .pending，不丢数据。
    4. 写成功后清理同目标的过期 .pending（已被本次内容取代）。

    Returns True on success (含落 .pending 的降级成功).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    delay = 0.2

    def _write_snapshot(target: Path) -> bool:
        """把完整内容快照写到 target（fsync 落盘）。"""
        with open(target, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        return True

    # 步骤 1：写 tmp（同目录、进程唯一），失败退避重试
    tmp_ok = False
    for attempt in range(retries):
        try:
            tmp_ok = _write_snapshot(tmp)
            break
        except PermissionError:
            # tmp 本身被锁（罕见）→ 直接把快照落 .pending
            try:
                _write_snapshot(_pending_path(path))
                return True
            except OSError:
                pass
        except OSError:
            pass
        time.sleep(delay * (attempt + 1))
    if not tmp_ok:
        return False

    # 步骤 2：原子替换目标；PermissionError（OneDrive/杀软瞬时锁）退避重试
    for replace_attempt in range(retries):
        try:
            os.replace(str(tmp), str(path))
            # 成功：清理已被本次内容取代的 .pending 快照
            pending = _pending_path(path)
            if pending.exists() and _mtime(pending) <= _mtime(path):
                try:
                    pending.unlink()
                except OSError:
                    pass
            return True
        except PermissionError:
            time.sleep(delay * (replace_attempt + 1))
        except OSError:
            break

    # 步骤 3：目标持续锁定 → 完整快照落 .pending（读端会优先采用更新的快照）
    try:
        _write_snapshot(_pending_path(path))
        try:
            tmp.unlink()
        except OSError:
            pass
        return True
    except OSError:
        pass
    return False


def _safe_read_text(path, default: str = "", encoding: str = "utf-8", max_size: int = 50 * 1024 * 1024) -> str:
    """Read text from file with retry on PermissionError.

    v2.2.2: 若存在比主文件**更新**的 .pending 快照（上次写入时目标被锁），
    优先返回快照内容——追加/读改写流程基于最新数据构建，不丢已写内容。

    Returns default if file doesn't exist or can't be read.
    Files larger than max_size (default 50MB) are truncated to avoid MemoryError.
    """
    target = Path(path)
    pending = _pending_path(target)
    try:
        if pending.exists() and (not target.exists() or _mtime(pending) > _mtime(target)):
            target = pending  # 快照是最新一次写入的完整内容
    except OSError:
        pass
    if not target.exists():
        # 主文件与快照都不存在 → 默认值
        return default

    for attempt in range(3):
        try:
            # 检查文件大小，超大文件截断读取避免 MemoryError
            try:
                file_size = target.stat().st_size
            except OSError:
                file_size = 0

            if file_size > max_size:
                # 只读取最后 max_size 字节
                with open(target, "r", encoding=encoding, errors="replace") as f:
                    f.seek(max(0, file_size - max_size))
                    return f.read()
            else:
                with open(target, "r", encoding=encoding, errors="replace") as f:
                    return f.read()
        except PermissionError:
            # Try reading .pending file
            try:
                if pending.exists() and pending != target:
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


# ---------------------------------------------------------------------------
# 公共 API 别名（v2.2.2）：模块内一律通过别名调用，杜绝新的 in-place 写入
# ---------------------------------------------------------------------------
safe_write_text = _safe_write_text
safe_read_text = _safe_read_text
